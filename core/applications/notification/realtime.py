"""
Live delivery over WebSockets (Django Channels). Each signed-in tab joins one
group per (organization, user); the server pushes new notifications and the
unread count to it. Pushing is best-effort: the notification is already saved,
so a dropped push only means the client catches up from `unread-count/`.
"""

from __future__ import annotations

import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

logger = logging.getLogger("notifications")


def group_name(organization_id, user_id) -> str:
    """Channels group names: ASCII letters, digits, '-', '_', '.', under 100 chars."""
    return f"notifications.{str(organization_id).replace('-', '')}.{user_id}"


def _send(group: str, message: dict) -> None:
    layer = get_channel_layer()
    if layer is None:
        return
    try:
        async_to_sync(layer.group_send)(group, message)
    except Exception:  # noqa: BLE001 - live push must never break delivery
        logger.exception("notification.push_failed", extra={"group": group})


def push_notifications(notifications, unread_by_user: dict, updated_ids=frozenset()) -> None:
    """
    `updated` tells the client a grouped notification it may already show changed
    (same `id`, new message/count): replace it in place and move it to the top.
    """
    from core.applications.notification.api.serializers import NotificationSerializer

    for notification in notifications:
        _send(
            group_name(notification.organization_id, notification.recipient_id),
            {
                "type": "notification.created",
                "notification": NotificationSerializer(notification).data,
                "unread": unread_by_user.get(notification.recipient_id, 0),
                "updated": notification.pk in updated_ids,
            },
        )


def push_unread_count(organization_id, user_id, unread: int) -> None:
    """After read / read-all, so every open tab updates its badge."""
    _send(group_name(organization_id, user_id), {"type": "notification.unread", "unread": unread})
