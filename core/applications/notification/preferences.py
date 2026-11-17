"""
An organization's notification settings: the catalogue defaults
(notification/events.py) merged with the rows it changed (NotificationSetting).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from django.db import transaction
from rest_framework.exceptions import ValidationError

from core.applications.notification.audit.action import AuditAction
from core.applications.notification.audit.services import record as audit_record
from core.applications.notification.events import EVENTS
from core.applications.notification.events import EventSpec
from core.applications.notification.models import NotificationSetting
from core.applications.users.models import Role


@dataclass(frozen=True)
class EffectiveSetting:
    spec: EventSpec
    enabled: bool
    in_app: bool
    email: bool
    roles: tuple[Role, ...]
    options: dict[str, Any]
    customized: bool

    @property
    def role_ids(self) -> list:
        return [role.pk for role in self.roles]


def _default_options(spec: EventSpec) -> dict[str, Any]:
    return {name: option[1] for name, option in spec.options.items()}


def effective_settings(organization, events: list[str] | None = None) -> dict[str, EffectiveSetting]:
    """Every event's setting for this organization, in two queries."""
    codes = events or list(EVENTS)
    roles_by_slug = {role.slug: role for role in Role.objects.filter(organization=organization)}
    rows = {
        row.event: row
        for row in NotificationSetting.objects.filter(organization=organization, event__in=codes)
        .prefetch_related("recipient_roles")
    }
    result = {}
    for code in codes:
        spec, row = EVENTS[code], rows.get(code)
        if row is None:
            result[code] = EffectiveSetting(
                spec=spec, enabled=True, in_app=True, email=spec.default_email,
                roles=tuple(roles_by_slug[slug] for slug in spec.default_roles if slug in roles_by_slug),
                options=_default_options(spec), customized=False,
            )
        else:
            result[code] = EffectiveSetting(
                spec=spec, enabled=row.enabled, in_app=row.in_app, email=row.email,
                roles=tuple(sorted(row.recipient_roles.all(), key=lambda role: role.name)),
                options={**_default_options(spec), **(row.options or {})}, customized=True,
            )
    return result


def setting_for(organization, event: str) -> EffectiveSetting:
    return effective_settings(organization, [event])[event]


def _clean_options(spec: EventSpec, raw: Any) -> dict[str, Any]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValidationError({"options": ["Must be an object."]})
    unknown = set(raw) - set(spec.options)
    if unknown:
        raise ValidationError({"options": [f"Unknown option(s) for {spec.code}: {', '.join(sorted(unknown))}."]})
    cleaned = {}
    for name, value in raw.items():
        kind, _default, low, high, _help = spec.options[name]
        try:
            value = kind(value)
        except (TypeError, ValueError):
            raise ValidationError({"options": [f"{name} must be a number."]}) from None
        if not low <= value <= high:
            raise ValidationError({"options": [f"{name} must be between {low} and {high}."]})
        cleaned[name] = value
    return cleaned


@transaction.atomic
def update_settings(organization, *, actor, changes: list[dict[str, Any]], audit_context=None) -> None:
    """
    Apply a list of per-event changes (any subset of enabled / in_app / email /
    recipient_role_ids / options). Fields left out keep their current value.
    """
    current = effective_settings(organization, [change["event"] for change in changes])
    # Keyed by UUID so an id with or without dashes matches the same role.
    org_roles = {uuid.UUID(str(role.pk)): role for role in Role.objects.filter(organization=organization)}

    for change in changes:
        code = change["event"]
        spec, now = EVENTS[code], current[code]
        roles = list(now.roles)
        if "recipient_role_ids" in change:
            roles = []
            for raw in change["recipient_role_ids"]:
                try:
                    role = org_roles.get(uuid.UUID(str(raw).strip()))
                except ValueError:
                    role = None
                if role is None:
                    raise ValidationError({"recipient_role_ids": [f"Unknown role for this organization: {raw}."]})
                if role not in roles:
                    roles.append(role)

        row, _created = NotificationSetting.objects.select_for_update().get_or_create(
            organization=organization, event=code,
        )
        before = {"enabled": now.enabled, "in_app": now.in_app, "email": now.email,
                  "roles": [r.slug for r in now.roles], "options": now.options}
        row.enabled = change.get("enabled", now.enabled)
        row.in_app = change.get("in_app", now.in_app)
        row.email = change.get("email", now.email)
        row.options = {**now.options, **_clean_options(spec, change.get("options"))}
        row.updated_by = actor
        row.save()
        row.recipient_roles.set(roles)

        audit_record(
            action=AuditAction.NOTIFICATION_SETTINGS_UPDATED,
            organization=organization,
            actor=actor,
            resource=row,
            previous_values=before,
            new_values={"enabled": row.enabled, "in_app": row.in_app, "email": row.email,
                        "roles": [r.slug for r in roles], "options": row.options},
            context=audit_context,
        )


# --------------------------------------------------------------------------- #
# Personal preferences ("My notifications")
# --------------------------------------------------------------------------- #
def is_locked(spec: EventSpec, role_slug: str) -> bool:
    """Security alerts can't be muted by the roles responsible for the account."""
    return role_slug in spec.mandatory_for_roles


def personal_preferences(membership) -> list[dict[str, Any]]:
    """
    Every event as this member experiences it: whether the organization sends it
    to them at all (`receives`), the organization's channels, and their own choice.
    """
    from core.applications.notification.models import UserNotificationPreference

    organization, role = membership.organization, membership.role
    current = effective_settings(organization)
    mine = {
        pref.event: pref
        for pref in UserNotificationPreference.objects.filter(user=membership.user, organization=organization)
    }
    rows = []
    for code, setting in current.items():
        spec, pref = setting.spec, mine.get(code)
        role_receives = any(r.pk == role.pk for r in setting.roles)
        locked = is_locked(spec, role.slug)
        rows.append(
            {
                "event": code,
                "category": spec.category,
                "label": spec.label,
                "description": spec.description,
                # Requesters always hear about their own approvals, whatever their role.
                "receives": setting.enabled and spec.triggered and (role_receives or code == "approval_decision"),
                "organization_in_app": setting.enabled and setting.in_app,
                "organization_email": setting.enabled and setting.email,
                "in_app": True if locked or pref is None else pref.in_app,
                "email": True if locked or pref is None else pref.email,
                "locked": locked,
            }
        )
    return rows


@transaction.atomic
def update_personal_preferences(membership, changes: list[dict[str, Any]]) -> None:
    from core.applications.notification.models import UserNotificationPreference

    for change in changes:
        spec = EVENTS[change["event"]]
        if is_locked(spec, membership.role.slug) and (change.get("in_app") is False or change.get("email") is False):
            raise ValidationError(
                {"event": [f"'{spec.label}' is a security alert and can't be turned off for your role."]}
            )
        pref, _ = UserNotificationPreference.objects.select_for_update().get_or_create(
            user=membership.user, organization=membership.organization, event=spec.code,
        )
        pref.in_app = change.get("in_app", pref.in_app)
        pref.email = change.get("email", pref.email)
        pref.save(update_fields=["in_app", "email", "updated_at"])


def muted_channels(organization, event: str, memberships) -> dict[Any, tuple[bool, bool]]:
    """
    {user_id: (in_app_on, email_on)} for the given recipients' personal choices, in
    one query. Users with no preference row (the common case) are left out = both on.
    """
    from core.applications.notification.models import UserNotificationPreference

    spec = EVENTS[event]
    locked_users = {m.user_id for m in memberships if is_locked(spec, m.role.slug)}
    prefs = UserNotificationPreference.objects.filter(
        organization=organization, event=event, user_id__in=[m.user_id for m in memberships]
    ).values_list("user_id", "in_app", "email")
    return {user_id: (in_app, email) for user_id, in_app, email in prefs if user_id not in locked_users}
