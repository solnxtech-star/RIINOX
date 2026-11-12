from __future__ import annotations

import hashlib
import secrets
from collections.abc import Iterable
from dataclasses import dataclass
from dataclasses import field
from datetime import timedelta
from functools import partial

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from core.applications.notification.audit import services as audit
from core.applications.notification.audit.action import AuditAction
from core.applications.notification.audit.context import AuditContext
from core.applications.users.defaults import ADMINISTRATOR_ROLE_SLUG
from core.applications.users.defaults import OWNER_ROLE_SLUG
from core.applications.users.email import deliver_invitation_email
from core.applications.users.errors import InvitationErrorCode as Err
from core.applications.users.errors import fail
from core.applications.users.models import Invitation
from core.applications.users.models import InvitationLocation
from core.applications.users.models import Membership
from core.applications.users.models import MembershipLocation
from core.applications.users.models import Organization
from core.applications.users.models import Role
from core.applications.users.services import get_seat_limit
from core.applications.users.services import seats_in_use
from core.applications.warehouse.models import Warehouse
from core.helper.enums import InvitationStatus
from core.helper.enums import PermissionCode

INVITATION_TTL = timedelta(days=getattr(settings, "INVITATION_TTL_DAYS", 7))
RESEND_COOLDOWN = timedelta(
    seconds=getattr(settings, "INVITATION_RESEND_COOLDOWN_SECONDS", 60)
)

# CONCURRENCY RULE: every function that changes members or invitations takes
# the organization row lock FIRST (_lock_organization). That one lock serializes
# invites, resends, revokes and accepts per tenant, so seat counts can't be
# raced and two operations can never deadlock on opposite lock orders.


# --------------------------------------------------------------------------- #
# Tokens
# --------------------------------------------------------------------------- #
def hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode()).hexdigest()


def _generate_token() -> tuple[str, str]:
    raw = secrets.token_urlsafe(32)
    return raw, hash_token(raw)


@dataclass(frozen=True)
class IssuedInvitation:
    """
    The raw token exists only here and in the email. Never return it from an API.
    """

    invitation: Invitation
    raw_token: str = field(repr=False)


def mask_email(email: str) -> str:
    """
    Mask an email address for display in audit logs, error messages, etc.
    Example: "j***@gmail.com"
    """
    local, _, domain = email.partition("@")
    return f"{local[:1]}{'*' * max(len(local) - 1, 2)}@{domain}"


# --------------------------------------------------------------------------- #
# Internal helpers
# --------------------------------------------------------------------------- #
def _lock_organization(
    organization_id, *, error: Err = Err.ORGANIZATION_NOT_FOUND
) -> Organization:
    try:
        return Organization.objects.select_for_update().get(pk=organization_id, is_active=True)
    except Organization.DoesNotExist:
        raise fail(error) from None


def _authorize(organization, actor, permission_code: PermissionCode) -> Membership:
    """
    Business-logic permission check (PRD §29, layer 3). The API layer already
    checked, but services never assume their caller did.
    """
    membership = (
        Membership.objects.effective()
        .for_organization(organization)
        .for_user(actor)
        .select_related("role")
        .first()
    )
    if membership is None:
        raise fail(Err.NOT_ORGANIZATION_MEMBER)
    if not Membership.objects.filter(pk=membership.pk).granting(permission_code).exists():
        raise fail(Err.PERMISSION_DENIED)
    return membership


def _is_owner(membership: Membership) -> bool:
    return membership.role.is_system and membership.role.slug == OWNER_ROLE_SLUG


def _assert_role_assignable(actor_membership: Membership, role, organization) -> None:
    """
    - A role must belong to this organization (tenant boundary).
    - Owner is never assignable through an invitation.
    - Only the Owner may handle Administrator invitations.
    """
    if role.organization_id != organization.pk:
        raise fail(Err.INVALID_ROLE)
    if not role.is_system:
        return
    if role.slug == OWNER_ROLE_SLUG:
        raise fail(Err.ROLE_NOT_ASSIGNABLE, "The Owner role cannot be assigned through an invitation.")
    if role.slug == ADMINISTRATOR_ROLE_SLUG and not _is_owner(actor_membership):
        raise fail(Err.ROLE_NOT_ASSIGNABLE, "Only the Owner can manage Administrator invitations.")


def _assert_not_member(organization, email: str) -> None:
    membership = Membership.objects.for_organization(organization).filter(user__email__iexact=email).first()
    if membership is not None:
        raise fail(Err.ALREADY_MEMBER if membership.is_active else Err.MEMBERSHIP_INACTIVE)


def _assert_seat_available(organization) -> None:
    limit = get_seat_limit(organization)
    if limit is not None and seats_in_use(organization) >= limit:
        raise fail(Err.USER_LIMIT_REACHED, limit=limit)


def _get_organization_invitation(organization, invitation_id) -> Invitation:
    """Tenant-scoped lookup: another organization's invitation id is simply 'not found'."""
    invitation = (
        Invitation.objects.for_organization(organization)
        .select_related("role")
        .filter(pk=invitation_id)
        .first()
    )
    if invitation is None:
        raise fail(Err.INVITATION_NOT_FOUND)
    return invitation


def _send_after_commit(invitation: Invitation, raw_token: str) -> None:
    # Never email for a transaction that rolled back.
    transaction.on_commit(
        partial(deliver_invitation_email, invitation, raw_token)
    )


def _retire_expired_pending(organization, email: str, *, actor, now, audit_context) -> None:
    """
    Expiry is derived, so an expired invitation still has status 'pending' and
    would trip the one-pending-invite-per-email constraint. Re-inviting retires
    it (status 'revoked', with an audit trail) before the new one is created.
    """
    existing = (
        Invitation.objects.for_organization(organization)
        .for_email(email)
        .filter(status=InvitationStatus.PENDING)
        .first()
    )
    if existing is None:
        return
    if not existing.is_expired:
        raise fail(Err.ALREADY_INVITED, invitation_id=str(existing.pk))

    existing.status = InvitationStatus.REVOKED
    existing.revoked_at = now
    existing.save()
    audit.record(
        action=AuditAction.INVITATION_REVOKED,
        organization=organization,
        actor=actor,
        resource=existing,
        reason="Superseded: expired invitation re-issued.",
        context=audit_context,
    )


# --------------------------------------------------------------------------- #
# Assignable choices (feed the invite form dropdowns and validate input)
# --------------------------------------------------------------------------- #
def assignable_roles(organization, actor_membership):
    """
    Roles this actor may offer in an invitation. Feeds the "Assign Role"
    dropdown and validates create input; _assert_role_assignable stays as the
    final server-side guard.
    """
    roles = Role.objects.for_organization(organization).exclude(
        is_system=True, slug=OWNER_ROLE_SLUG
    )
    if not _is_owner(actor_membership):
        roles = roles.exclude(is_system=True, slug=ADMINISTRATOR_ROLE_SLUG)
    return roles


def assignable_locations(organization, actor_membership):
    """
    Locations this actor may put an invitee into. Feeds the "Assign Location"
    dropdown and validates create input. An actor limited to some locations
    can't grant others (no privilege escalation).
    """
    queryset = Warehouse.objects.filter(organization=organization, is_active=True).order_by(
        "-is_default", "name"
    )
    if not actor_membership.all_locations:
        queryset = queryset.filter(pk__in=actor_membership.locations.values("pk"))
    return queryset


def _resolve_location_scope(organization, actor_membership, role, *, all_locations, locations):
    """
    Returns (all_locations, [Warehouse, ...]). The serializer already checks
    shape; this is the real boundary, so it re-checks tenant and scope rules.
    """
    requested = {location.pk: location for location in locations}

    if all_locations and requested:
        raise fail(Err.INVALID_LOCATION, "Send either all_locations or a list of locations, not both.")
    if not all_locations and not requested:
        raise fail(Err.LOCATION_REQUIRED, "Choose at least one location, or all locations.")

    is_admin_role = role.is_system and role.slug == ADMINISTRATOR_ROLE_SLUG
    if is_admin_role and not all_locations:
        raise fail(Err.LOCATION_SCOPE_NOT_ALLOWED, "Administrators always have access to all locations.")

    if all_locations:
        if not actor_membership.all_locations:
            raise fail(Err.LOCATION_SCOPE_NOT_ALLOWED, "You can't grant access to locations you don't have.")
        return True, []

    allowed_ids = set(
        assignable_locations(organization, actor_membership)
        .filter(pk__in=requested)
        .values_list("pk", flat=True)
    )
    if allowed_ids != set(requested):
        raise fail(Err.INVALID_LOCATION, "One or more locations are invalid or not available to you.")
    return False, [requested[pk] for pk in allowed_ids]


# --------------------------------------------------------------------------- #
# Team side (authenticated, organization-scoped)
# --------------------------------------------------------------------------- #
@transaction.atomic
def create_invitation(
    *,
    organization,
    inviter,
    email: str,
    role,
    name: str = "",
    all_locations: bool = False,
    locations: Iterable[Warehouse] = (),
    audit_context: AuditContext | None = None,
) -> IssuedInvitation:
    org = _lock_organization(organization.pk)
    actor = _authorize(org, inviter, PermissionCode.INVITE_TEAM_MEMBER)
    _assert_role_assignable(actor, role, org)
    all_locations, location_list = _resolve_location_scope(
        org, actor, role, all_locations=all_locations, locations=locations
    )

    email = email.strip().lower()
    now = timezone.now()

    _assert_not_member(org, email)
    _retire_expired_pending(org, email, actor=inviter, now=now, audit_context=audit_context)
    _assert_seat_available(org)

    raw_token, token_hash = _generate_token()
    invitation = Invitation.objects.create(
        organization=org,
        name=(name or "").strip(),
        email=email,
        role=role,
        all_locations=all_locations,
        token_hash=token_hash,
        expires_at=now + INVITATION_TTL,
        last_sent_at=now,
        invited_by=inviter,
    )
    InvitationLocation.objects.bulk_create(
        [InvitationLocation(invitation=invitation, location=loc) for loc in location_list]
    )

    audit.record(
        action=AuditAction.INVITATION_CREATED,
        organization=org,
        actor=inviter,
        resource=invitation,
        new_values={
            "email": invitation.email,
            "name": invitation.name,
            "role": role.slug,
            "all_locations": all_locations,
            "locations": sorted(str(loc.pk) for loc in location_list),
            "expires_at": invitation.expires_at.isoformat(),
        },
        context=audit_context,
    )
    _send_after_commit(invitation, raw_token)
    return IssuedInvitation(invitation=invitation, raw_token=raw_token)


@transaction.atomic
def resend_invitation(
    *, organization, invitation_id, actor, audit_context: AuditContext | None = None
) -> IssuedInvitation:
    """Rotates the token (the old link stops working) and extends the expiry."""
    org = _lock_organization(organization.pk)
    actor_membership = _authorize(org, actor, PermissionCode.INVITE_TEAM_MEMBER)
    invitation = _get_organization_invitation(org, invitation_id)
    _assert_role_assignable(actor_membership, invitation.role, org)

    if invitation.status != InvitationStatus.PENDING:
        raise fail(Err.INVITATION_NOT_PENDING)

    now = timezone.now()
    if invitation.last_sent_at and now - invitation.last_sent_at < RESEND_COOLDOWN:
        retry_after = int((RESEND_COOLDOWN - (now - invitation.last_sent_at)).total_seconds()) + 1
        raise fail(Err.INVITATION_RESEND_TOO_SOON, retry_after=retry_after)

    # An expired invitation holds no seat; reviving it needs one.
    if invitation.is_expired:
        _assert_seat_available(org)

    previous_expiry = invitation.expires_at
    raw_token, token_hash = _generate_token()
    invitation.token_hash = token_hash
    invitation.expires_at = now + INVITATION_TTL
    invitation.last_sent_at = now
    invitation.save()

    audit.record(
        action=AuditAction.INVITATION_RESENT,
        organization=org,
        actor=actor,
        resource=invitation,
        previous_values={"expires_at": previous_expiry.isoformat()},
        new_values={"expires_at": invitation.expires_at.isoformat()},
        context=audit_context,
    )
    _send_after_commit(invitation, raw_token)
    return IssuedInvitation(invitation=invitation, raw_token=raw_token)


@transaction.atomic
def revoke_invitation(
    *, organization, invitation_id, actor, reason: str = "", audit_context: AuditContext | None = None
) -> Invitation:
    org = _lock_organization(organization.pk)
    actor_membership = _authorize(org, actor, PermissionCode.REVOKE_INVITATION)
    invitation = _get_organization_invitation(org, invitation_id)
    _assert_role_assignable(actor_membership, invitation.role, org)

    if invitation.status != InvitationStatus.PENDING:
        raise fail(Err.INVITATION_NOT_PENDING)

    invitation.status = InvitationStatus.REVOKED
    invitation.revoked_at = timezone.now()
    invitation.save()  # the record is kept, never deleted (PRD §44)

    audit.record(
        action=AuditAction.INVITATION_REVOKED,
        organization=org,
        actor=actor,
        resource=invitation,
        previous_values={"status": InvitationStatus.PENDING},
        new_values={"status": InvitationStatus.REVOKED},
        reason=reason,
        context=audit_context,
    )
    return invitation


# --------------------------------------------------------------------------- #
# Public side (token holder; no organization context yet)
# --------------------------------------------------------------------------- #
def get_invitation_preview(token: str) -> Invitation:
    """Read-only lookup for the public validate endpoint. Unknown, revoked and used tokens look identical."""
    invitation = Invitation.objects.with_related().filter(token_hash=hash_token(token)).first()
    if (
        invitation is None
        or invitation.status != InvitationStatus.PENDING
        or not invitation.organization.is_active
    ):
        raise fail(Err.INVITATION_INVALID)
    if invitation.is_expired:
        raise fail(Err.INVITATION_EXPIRED)
    return invitation


@transaction.atomic
def accept_invitation(*, token: str, user, audit_context: AuditContext | None = None) -> Membership:
    """
    Consumes the invitation and creates the Membership (and its location
    links), atomically. Replaying the same accept for the same user returns the
    existing membership (PRD §45).

    This is the only path that creates a membership without an existing one,
    so it is the only path that must check the seat limit. The organization
    row is locked first, like every other member/invitation operation.
    """
    token_hash = hash_token(token)

    # Find the tenant first, lock it, then re-read: keeps the org-first lock order.
    organization_id = (
        Invitation.objects.filter(token_hash=token_hash).values_list("organization_id", flat=True).first()
    )
    if organization_id is None:
        raise fail(Err.INVITATION_INVALID)
    org = _lock_organization(organization_id, error=Err.INVITATION_INVALID)

    invitation = Invitation.objects.select_related("role").filter(token_hash=token_hash).first()
    if invitation is None:
        raise fail(Err.INVITATION_INVALID)

    if invitation.status == InvitationStatus.ACCEPTED:
        if invitation.accepted_by_id == user.pk:
            existing = (
                Membership.objects.filter(invitation=invitation).select_related("organization", "role").first()
            )
            if existing is not None:
                return existing
        raise fail(Err.INVITATION_INVALID)
    if invitation.status != InvitationStatus.PENDING:
        raise fail(Err.INVITATION_INVALID)
    if invitation.is_expired:
        raise fail(Err.INVITATION_EXPIRED)

    # The email match is the security basis of this flow, so it must be a verified address.
    if not user.is_verified:
        raise fail(Err.EMAIL_NOT_VERIFIED)
    if user.email.strip().lower() != invitation.email:
        raise fail(Err.INVITATION_EMAIL_MISMATCH)

    existing_membership = Membership.objects.filter(user=user, organization=org).first()
    if existing_membership is not None:
        raise fail(Err.ALREADY_MEMBER if existing_membership.is_active else Err.MEMBERSHIP_INACTIVE)

    if invitation.role.organization_id != org.pk:
        raise fail(Err.INVALID_ROLE)

    # Self-healing: a membership already points at this still-pending invitation
    # (an earlier run created it but never marked the invitation accepted).
    # Membership.invitation is one-to-one, so creating another would crash.
    linked = Membership.objects.filter(invitation=invitation).first()
    if linked is not None:
        if linked.user_id != user.pk:
            raise fail(Err.INVITATION_INVALID)
        invitation.status = InvitationStatus.ACCEPTED
        invitation.accepted_by = user
        invitation.accepted_at = invitation.accepted_at or timezone.now()
        invitation.save()
        return linked

    # This invitation already occupies a seat and becomes a member's seat, so the
    # count doesn't change on accept. Only block if the plan has since dropped
    # below current usage (e.g. a downgrade).
    limit = get_seat_limit(org)
    if limit is not None and seats_in_use(org) > limit:
        raise fail(Err.USER_LIMIT_REACHED, limit=limit)

    # Re-check locations at accept time: they may have been deactivated since the invite was sent.
    if invitation.all_locations:
        locations = []
    else:
        locations = list(
            Warehouse.objects.filter(
                organization=org, is_active=True, invitation_links__invitation=invitation
            )
        )
        if not locations:
            raise fail(Err.INVITATION_LOCATIONS_UNAVAILABLE)

    membership = Membership.objects.create(
        user=user,
        organization=org,
        role=invitation.role,
        invitation=invitation,
        all_locations=invitation.all_locations,
        is_active=True,
    )
    MembershipLocation.objects.bulk_create(
        [MembershipLocation(membership=membership, location=loc) for loc in locations]
    )

    invitation.status = InvitationStatus.ACCEPTED
    invitation.accepted_by = user
    invitation.accepted_at = timezone.now()
    invitation.save()

    # The invite form may have collected a name; use it if the new account has none.
    if not user.name and invitation.name:
        user.name = invitation.name
        user.save(update_fields=["name"])

    audit.record(
        action=AuditAction.INVITATION_ACCEPTED,
        organization=org,
        actor=user,
        resource=invitation,
        new_values={
            "membership_id": str(membership.pk),
            "role": invitation.role.slug,
            "all_locations": invitation.all_locations,
            "locations": sorted(str(loc.pk) for loc in locations),
        },
        context=audit_context,
    )
    return membership
