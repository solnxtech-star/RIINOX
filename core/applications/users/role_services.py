"""
Roles & permissions management (PRD §28, §55): custom roles and editable
permission sets. Views call these; every rule lives here.

Rules
- Owner and Administrator are locked: they always hold every permission, so
  nobody can lock the business out of its own account.
- Other built-in roles (Manager, Accountant, ...) keep their name and can't be
  deleted, but their permission set can be changed.
- Custom roles can be renamed, re-permissioned and deleted (only when nobody uses them).
- No privilege escalation: an actor can only add or remove permissions they hold
  themselves. The Owner holds everything.
- Every change is audited with the before/after permission codes.

A permission change applies on the holder's next request: permissions are read
from the database on every check, nothing is cached in the token.
"""

from __future__ import annotations

from collections.abc import Iterable

from django.db import IntegrityError
from django.db import transaction
from django.db.models import Count
from django.db.models import Q
from django.utils.text import slugify

from core.applications.notification.audit import services as audit
from core.applications.notification.audit.action import AuditAction
from core.applications.notification.audit.context import AuditContext
from core.applications.users.defaults import ADMINISTRATOR_ROLE_SLUG
from core.applications.users.defaults import OWNER_ROLE_SLUG
from core.applications.users.defaults import PERMISSION_MODULES
from core.applications.users.models import Permission
from core.applications.users.models import Role
from core.applications.users.models import RolePermission
from core.helper.enums import InvitationStatus
from core.helper.enums import PermissionCode

LOCKED_ROLE_SLUGS = frozenset({OWNER_ROLE_SLUG, ADMINISTRATOR_ROLE_SLUG})
MAX_NAME_LENGTH = 100


def _fail(code, message=None, **extra):
    from core.applications.users.errors import fail  # errors imports services: keep this lazy

    return fail(code, message, **extra)


def _codes():
    from core.applications.users.errors import RoleErrorCode

    return RoleErrorCode


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------
def is_locked(role: Role) -> bool:
    return role.is_system and role.slug in LOCKED_ROLE_SLUGS


def roles_with_usage(organization):
    """The organization's roles with member counts and permissions preloaded (built-in first)."""
    return (
        Role.objects.for_organization(organization)
        .annotate(member_count=Count("memberships", filter=Q(memberships__is_active=True), distinct=True))
        .prefetch_related("permissions")
        .ordered()
    )


def permission_catalog() -> list[dict]:
    """Every permission grouped by module, in the order of PERMISSION_MODULES (for the checkbox screen)."""
    by_code = {p.code: p for p in Permission.objects.all()}
    groups = []
    for module, codes in PERMISSION_MODULES.items():
        permissions = [by_code[str(code)] for code in codes if str(code) in by_code]
        if permissions:
            groups.append({
                "module": module,
                "label": module.replace("_", " ").title(),
                "permissions": [
                    {"code": p.code, "name": p.name, "description": p.description or ""} for p in permissions
                ],
            })
    return groups


def actor_permission_codes(actor_membership) -> frozenset[str]:
    """What the actor may hand out: everything for the Owner, otherwise their role's permissions."""
    if actor_membership is None:
        return frozenset()
    role = actor_membership.role
    if role.is_system and role.slug == OWNER_ROLE_SLUG:
        return frozenset(PermissionCode.values)
    return frozenset(role.permissions.values_list("code", flat=True))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _clean_name(name: str) -> str:
    name = " ".join((name or "").split())
    if not name:
        raise _fail(_codes().ROLE_NAME_REQUIRED)
    if len(name) > MAX_NAME_LENGTH:
        raise _fail(_codes().ROLE_NAME_TOO_LONG)
    return name


def _assert_name_free(organization, name: str, *, exclude_pk=None) -> None:
    taken = Role.objects.for_organization(organization).filter(name__iexact=name)
    if exclude_pk is not None:
        taken = taken.exclude(pk=exclude_pk)
    if taken.exists():
        raise _fail(_codes().ROLE_NAME_TAKEN, f"A role named '{name}' already exists.")


def _unique_slug(organization, name: str) -> str:
    base = (slugify(name) or "role")[:90]
    existing = set(
        Role.objects.for_organization(organization).filter(slug__startswith=base).values_list("slug", flat=True)
    )
    slug, n = base, 2
    while slug in existing:
        slug, n = f"{base}-{n}", n + 1
    return slug


def _resolve_permissions(codes: Iterable[str]) -> dict[str, Permission]:
    wanted = {str(code).strip().upper() for code in codes}
    found = {p.code: p for p in Permission.objects.filter(code__in=wanted)}
    unknown = sorted(wanted - found.keys())
    if unknown:
        raise _fail(_codes().UNKNOWN_PERMISSION, f"Unknown permission code(s): {', '.join(unknown)}.",
                    unknown=unknown)
    return found


def _assert_can_change(actor_membership, changed: set[str]) -> None:
    beyond = sorted(changed - actor_permission_codes(actor_membership))
    if beyond:
        raise _fail(
            _codes().PERMISSION_ESCALATION,
            "You can only add or remove permissions you hold yourself. Not allowed: " + ", ".join(beyond) + ".",
            permissions=beyond,
        )


def _set_permissions(role: Role, wanted: dict[str, Permission]) -> tuple[list[str], list[str]]:
    """Replace the role's permissions with `wanted`; returns (added, removed) codes."""
    current = set(role.permissions.values_list("code", flat=True))
    added, removed = sorted(wanted.keys() - current), sorted(current - wanted.keys())
    if removed:
        RolePermission.objects.filter(role=role, permission__code__in=removed).delete()
    if added:
        RolePermission.objects.bulk_create(
            [RolePermission(role=role, permission=wanted[code]) for code in added], ignore_conflicts=True
        )
    return added, removed


def _lock_role(organization, role_id) -> Role:
    role = Role.objects.select_for_update().filter(organization=organization, pk=role_id).first()
    if role is None:
        raise _fail(_codes().ROLE_NOT_FOUND)
    return role


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------
@transaction.atomic
def create_role(*, organization, actor, actor_membership, name: str, description: str = "",
                permissions: Iterable[str] = (), audit_context: AuditContext | None = None) -> Role:
    name = _clean_name(name)
    wanted = _resolve_permissions(permissions)
    _assert_can_change(actor_membership, set(wanted))
    _assert_name_free(organization, name)
    try:
        with transaction.atomic():
            role = Role.objects.create(
                organization=organization, name=name, slug=_unique_slug(organization, name),
                description=(description or "").strip(), is_system=False,
            )
    except IntegrityError:  # a concurrent create took the slug
        raise _fail(_codes().ROLE_NAME_TAKEN, f"A role named '{name}' already exists.") from None
    _set_permissions(role, wanted)
    audit.record(
        action=AuditAction.ROLE_CREATED, organization=organization, actor=actor, resource=role,
        new_values={"name": role.name, "permissions": sorted(wanted)}, context=audit_context,
    )
    return role


@transaction.atomic
def update_role(*, organization, role_id, actor, actor_membership, name: str | None = None,
                description: str | None = None, permissions: Iterable[str] | None = None,
                audit_context: AuditContext | None = None) -> Role:
    """Only the fields sent are changed. `permissions` replaces the whole set."""
    role = _lock_role(organization, role_id)
    if is_locked(role):
        raise _fail(_codes().ROLE_LOCKED)

    previous, new = {}, {}
    if name is not None or description is not None:
        if role.is_system:
            raise _fail(_codes().SYSTEM_ROLE_NOT_RENAMEABLE)
        fields = []
        if name is not None:
            name = _clean_name(name)
            if name != role.name:
                _assert_name_free(organization, name, exclude_pk=role.pk)
                previous["name"], new["name"] = role.name, name
                role.name = name
                fields.append("name")
        if description is not None and description.strip() != (role.description or ""):
            previous["description"], new["description"] = role.description or "", description.strip()
            role.description = description.strip()
            fields.append("description")
        if fields:
            role.save(update_fields=[*fields, "updated_at"])

    if permissions is not None:
        wanted = _resolve_permissions(permissions)
        current = set(role.permissions.values_list("code", flat=True))
        _assert_can_change(actor_membership, wanted.keys() ^ current)
        added, removed = _set_permissions(role, wanted)
        if added or removed:
            previous["permissions"], new["permissions"] = sorted(current), sorted(wanted)
            audit.record(
                action=AuditAction.ROLE_PERMISSIONS_CHANGED, organization=organization, actor=actor,
                resource=role, previous_values={"permissions": sorted(current)},
                new_values={"permissions": sorted(wanted), "added": added, "removed": removed},
                context=audit_context,
            )

    if "name" in new or "description" in new:
        audit.record(
            action=AuditAction.ROLE_UPDATED, organization=organization, actor=actor, resource=role,
            previous_values={k: v for k, v in previous.items() if k != "permissions"},
            new_values={k: v for k, v in new.items() if k != "permissions"}, context=audit_context,
        )
    return role


@transaction.atomic
def delete_role(*, organization, role_id, actor, actor_membership,
                audit_context: AuditContext | None = None) -> None:
    """
    Custom roles only, and only when nothing uses them. Members (active or not)
    and pending invitations must be moved to another role first; past
    invitations are kept as history (PRD §44), so a role that appears on one
    can't be deleted either.
    """
    role = _lock_role(organization, role_id)
    if role.is_system:
        raise _fail(_codes().SYSTEM_ROLE_NOT_DELETABLE)
    _assert_can_change(actor_membership, set(role.permissions.values_list("code", flat=True)))

    members = role.memberships.count()
    pending = role.invitations.filter(status=InvitationStatus.PENDING).count()
    past = role.invitations.exclude(status=InvitationStatus.PENDING).count()
    if members or pending or past:
        raise _fail(
            _codes().ROLE_IN_USE,
            "This role is in use. Move its members and pending invitations to another role first."
            if members or pending else
            "This role appears on past invitations, which are kept as history, so it can't be deleted.",
            members=members, pending_invitations=pending, past_invitations=past,
        )
    snapshot = {"name": role.name, "slug": role.slug,
                "permissions": sorted(role.permissions.values_list("code", flat=True))}
    audit.record(
        action=AuditAction.ROLE_DELETED, organization=organization, actor=actor, resource=role,
        previous_values=snapshot, context=audit_context,
    )
    role.delete()
