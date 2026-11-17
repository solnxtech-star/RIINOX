"""
Sending notifications: `notify(...)` is the one entry point every trigger calls.

- It runs **after the surrounding database transaction commits**, so a sale that
  fails and rolls back never produces an alert.
- It never raises into the caller: a notification problem must not break a sale
  or a stock movement. Failures are logged.
- Recipients are the active members holding one of the event's roles (plus any
  `extra_users`, e.g. the requester of an approval), limited to members who can
  access the event's branch, minus the person who caused it.
- The same `dedupe_key` within `dedupe_hours` is sent only once (a product that
  stays low isn't announced on every sale).
- In-app rows are written in one query; emails go out on a Celery worker.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
import uuid

from django.db import IntegrityError
from django.db import transaction
from django.db.models import Count
from django.utils import timezone
from django.db.models import Q

from core.applications.notification.events import EVENTS
from core.applications.notification.models import Notification
from core.applications.notification.preferences import muted_channels
from core.applications.notification.preferences import setting_for
from core.applications.notification.realtime import push_notifications

logger = logging.getLogger("notifications")


def notify(
    event: str,
    organization,
    *,
    title: str,
    message: str,
    location=None,
    reference=None,
    link: dict | None = None,
    data: dict | None = None,
    extra_users=(),
    exclude_user=None,
    dedupe_key: str = "",
    dedupe_hours: int = 24,
    group_item: str = "",
) -> None:
    """`group_item` identifies the occurrence inside a grouped event (e.g. branch:product)."""
    if event not in EVENTS:
        raise ValueError(f"Unknown notification event {event!r}.")  # programming error: fail loudly

    payload = dict(
        event=event, organization=organization, title=title, message=message, location=location,
        reference=reference, link=link, data=data or {}, extra_users=tuple(extra_users),
        exclude_user=exclude_user, dedupe_key=dedupe_key, dedupe_hours=dedupe_hours, group_item=group_item,
    )
    transaction.on_commit(lambda: _deliver_safely(payload))


def _deliver_safely(payload: dict[str, Any]) -> None:
    try:
        deliver(**payload)
    except Exception:  # noqa: BLE001 - alerts must never break the business action
        logger.exception(
            "notification.delivery_failed",
            extra={"event": payload["event"], "organization_id": str(payload["organization"].pk)},
        )


def recipient_memberships(setting, organization, *, location=None, extra_users=(), exclude_user=None):
    """Active memberships that should get this event (one per user), with user and role loaded."""
    from core.applications.users.models import Membership

    memberships = Membership.objects.effective().filter(organization=organization, user__is_active=True)
    by_role = Q(role__in=setting.roles) if setting.roles else Q(pk__in=[])
    extra_ids = [user.pk for user in extra_users if user is not None]
    memberships = memberships.filter(by_role | Q(user_id__in=extra_ids))
    if setting.spec.branch_scoped and location is not None:
        memberships = memberships.filter(Q(all_locations=True) | Q(membership_locations__location=location))
    unique = {m.user_id: m for m in memberships.select_related("user", "role")}
    if exclude_user is not None:
        unique.pop(exclude_user.pk, None)
    return list(unique.values())


def recipients_for(setting, organization, **kwargs):
    return [m.user for m in recipient_memberships(setting, organization, **kwargs)]


def _claim_dedupe(organization, event: str, key: str, hours: int) -> bool:
    """True the first time a key is seen in the window; False for repeats."""
    if not key:
        return True
    cache_key = f"notif:dedupe:{organization.pk}:{event}:{key}"
    return cache.add(cache_key, "1", timeout=hours * 3600)


def frontend_url(link: dict | None) -> str:
    base = (getattr(settings, "FRONTEND_URL", "") or "").rstrip("/")
    if not base or not link:
        return ""
    query = urlencode(link.get("query") or {})
    return f"{base}/{link['resource']}" + (f"?{query}" if query else "")


GROUP_ITEMS_KEPT = 100


def _same_location(location_id, location) -> bool:
    if location_id is None or location is None:
        return False
    return uuid.UUID(str(location_id)) == uuid.UUID(str(location.pk))


def _upsert_group(spec, user, fields: dict, group_item: str):
    """
    Add one occurrence to the person's open (unread) group for this event, or open
    a new group. Returns (notification, updated) or None when the item is already
    in the group. The unique constraint on open groups makes concurrent sales safe:
    the loser of an insert race retries and updates the winner's row.
    """
    for _attempt in range(2):
        try:
            with transaction.atomic():
                existing = (
                    Notification.objects.select_for_update()
                    .filter(organization=fields["organization"], recipient=user,
                            notification_type=spec.code, group_key=spec.code, is_read=False)
                    .first()
                )
                if existing is None:
                    created = Notification.objects.create(
                        recipient=user, group_key=spec.code,
                        **{**fields, "data": {**fields["data"], "count": 1, "items": [group_item]}},
                    )
                    return created, False

                items = list((existing.data or {}).get("items") or [])
                if group_item in items:
                    return None
                count = int((existing.data or {}).get("count", len(items))) + 1
                existing.message = spec.group_message.format(count=count)
                existing.link = spec.group_link
                existing.data = {**fields["data"], "count": count, "items": (items + [group_item])[-GROUP_ITEMS_KEPT:],
                                 "latest_message": fields["message"]}
                if not _same_location(existing.location_id, fields["location"]):
                    existing.location = None  # the group now spans branches
                existing.content_type, existing.object_id = None, ""  # many items: no single reference
                existing.created_at = timezone.now()  # moves to the top of the list
                existing.save(update_fields=["message", "link", "data", "location", "content_type",
                                             "object_id", "created_at", "updated_at"])
                return existing, True
        except IntegrityError:
            continue
    logger.warning("notification.group_upsert_gave_up", extra={"event": spec.code, "user_id": user.pk})
    return None


def deliver(*, event, organization, title, message, location, reference, link, data,
            extra_users, exclude_user, dedupe_key, dedupe_hours, group_item: str = "") -> int:
    """Synchronous delivery (used by notify() after commit, and by scheduled jobs). Returns recipients."""
    setting = setting_for(organization, event)
    if not setting.enabled or not (setting.in_app or setting.email):
        return 0
    memberships = recipient_memberships(setting, organization, location=location, extra_users=extra_users,
                                        exclude_user=exclude_user)
    if not memberships or not _claim_dedupe(organization, event, dedupe_key, dedupe_hours):
        return 0

    # Personal preferences can only switch channels off ("My notifications").
    personal = muted_channels(organization, event, memberships)
    in_app_users = [m.user for m in memberships if setting.in_app and personal.get(m.user_id, (True, True))[0]]
    email_users = [m.user for m in memberships if setting.email and personal.get(m.user_id, (True, True))[1]]

    if in_app_users:
        content_type = ContentType.objects.get_for_model(reference) if reference is not None else None
        fields = dict(
            organization=organization, notification_type=event, title=title, message=message,
            location=location, content_type=content_type,
            object_id=str(reference.pk) if reference is not None else "",
            link=link, data=data, dedupe_key=dedupe_key,
        )
        if setting.spec.groupable and group_item:
            touched = [
                result for user in in_app_users
                if (result := _upsert_group(setting.spec, user, fields, group_item)) is not None
            ]
        else:
            touched = [
                (notification, False)
                for notification in Notification.objects.bulk_create(
                    [Notification(recipient=user, **fields) for user in in_app_users]
                )
            ]
        if touched:
            unread = dict(
                Notification.objects.filter(
                    organization=organization, recipient__in=in_app_users, is_read=False
                ).values_list("recipient").annotate(total=Count("pk"))
            )
            push_notifications(
                [notification for notification, _ in touched],
                unread,
                updated_ids={notification.pk for notification, updated in touched if updated},
            )

    if email_users:
        emails = sorted({user.email for user in email_users if user.email})
        if emails:
            from core.applications.notification.tasks import send_notification_emails

            context = {
                "organization_name": organization.name, "title": title, "message": message,
                "location_name": getattr(location, "name", ""), "url": frontend_url(link),
                "event_label": setting.spec.label,
            }
            try:
                send_notification_emails.delay(emails, context)
            except Exception:  # noqa: BLE001 - broker down: keep the in-app alerts, log the miss
                logger.exception("notification.email_enqueue_failed", extra={"event": event})
    return len(memberships)
