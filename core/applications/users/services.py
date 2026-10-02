from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import asdict
from dataclasses import dataclass

from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.utils import timezone

from core.applications.invoice.services import provision_organization_documents
from core.applications.subscriptions.services import get_plan_limit
from core.applications.subscriptions.services import start_subscription
from core.applications.users import reference_data
from core.applications.users.defaults import ADMINISTRATOR_ROLE_SLUG
from core.applications.users.defaults import ALL
from core.applications.users.defaults import DEFAULT_ROLE_PERMISSIONS
from core.applications.users.defaults import DEFAULT_ROLES
from core.applications.users.defaults import MANAGER_ROLE_SLUGS
from core.applications.users.defaults import MODULE_BY_CODE
from core.applications.users.defaults import OWNER_ROLE_SLUG
from core.applications.users.models import BusinessType, Invitation
from core.applications.users.models import Membership
from core.applications.users.models import Organization
from core.applications.users.models import OrganizationBusinessType
from core.applications.users.models import OwnerMembershipDetail
from core.applications.users.models import Permission
from core.applications.users.models import Role
from core.applications.users.models import RolePermission
from core.applications.users.models import State
from core.helper.enums import PermissionCode
from core.helper.utils import default_invite_expiry
from core.helper.utils import send_invitation_email


class OrganizationServiceError(Exception):
    """Base class for expected, user-facing failures. The API layer translates these (api/errors.py)."""


class BusinessTypeSelectionError(OrganizationServiceError, ValueError):
    """The requested set of business types violates an invariant."""


class MembershipRuleViolation(OrganizationServiceError):
    """A membership/invitation rule was broken. `field` names the offending input, if any."""

    def __init__(self, message: str, *, field: str | None = None):
        super().__init__(message)
        self.message = message
        self.field = field


class InvitationInvalid(OrganizationServiceError):
    """The invitation can't be accepted (unknown, used, expired, or not for this user)."""


class NotAuthorized(OrganizationServiceError):
    """The actor lacks the role required for this operation."""


class PlanLimitReached(OrganizationServiceError):
    """The organization's plan doesn't allow this (e.g. no free user seats)."""


class OrganizationProfileInvalid(OrganizationServiceError, ValueError):
    """A profile field failed validation against reference data. `field` names which one."""

    def __init__(self, message: str, *, field: str):
        super().__init__(message)
        self.message = message
        self.field = field


# ---------------------------------------------------------------------------
# Organization creation
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class OrganizationProfile:
    """
    The organization fields onboarding may set, and nothing else. An explicit
    allow-list means a request can never smuggle `is_active`, `created_by` or
    any other column into `Organization.objects.create`.

    `country`, `currency` and `timezone` are the values the user picked from
    the reference-data dropdowns (GET /metadata/countries|currencies/), and
    `state` from GET /metadata/states/?country=. The serializer is expected to
    have already validated all four against `reference_data`; `create_organization`
    re-checks them defensively, since this function is the actual security
    boundary — it's callable from anywhere (a management command, a future
    admin action), not only from that one serializer.
    """

    name: str
    country: str
    currency: str
    state: str
    address: str
    phone: str
    staff_size: str
    timezone: str = "Africa/Lagos"
    postal_code: str = ""
    tax_id: str = ""
    registration_number: str = ""


def _validate_profile(profile: OrganizationProfile) -> None:
    """
    Defensive re-check of the reference-data fields. The serializer already
    validates these against the same functions, but a service is a boundary
    in its own right: whatever calls this later (a management command, an
    admin action, a bulk-import script) must not be able to skip the check
    just because it isn't a request going through that one serializer.

    Only catalog-backed fields are re-checked here (country, currency,
    timezone, state) — free-form fields like phone and registration_number
    are format-validated by the serializer, not looked up against a catalog,
    so there's nothing here for this function to re-verify.

    Checks stop at the first failure rather than collecting all of them: this
    is a defensive backstop for callers that bypass the serializer, not the
    primary source of field-level errors a normal request sees.
    """
    if not reference_data.is_valid_country(profile.country):
        raise OrganizationProfileInvalid(f"'{profile.country}' is not a recognised country code.", field="country")
    if not reference_data.is_valid_currency(profile.currency):
        raise OrganizationProfileInvalid(f"'{profile.currency}' is not a recognised currency code.", field="currency")
    if not reference_data.is_valid_timezone(profile.timezone):
        raise OrganizationProfileInvalid(f"'{profile.timezone}' is not a recognised timezone.", field="timezone")
    if not State.objects.filter(country=profile.country, name__iexact=profile.state).exists():
        raise OrganizationProfileInvalid(
            f"'{profile.state}' is not a recognised state for '{profile.country}'.", field="state"
        )


# ---------------------------------------------------------------------------
# Permission checks
# ---------------------------------------------------------------------------
def has_org_permission(user, organization: Organization, permission_code: str) -> bool:
    """True if the user's effective membership in `organization` grants `permission_code`."""
    if not getattr(user, "is_authenticated", False):
        return False
    return (
        Membership.objects.effective()
        .for_user(user)
        .for_organization(organization)
        .granting(permission_code)
        .exists()
    )


def has_org_role(user, organization: Organization, *role_slugs: str) -> bool:
    """True if the user holds one of the built-in roles (e.g. owner, administrator) in `organization`."""
    if not getattr(user, "is_authenticated", False):
        return False
    return (
        Membership.objects.effective()
        .for_user(user)
        .for_organization(organization)
        .with_system_role(*role_slugs)
        .exists()
    )


@transaction.atomic
def create_organization(*, user, business_type: BusinessType, profile: OrganizationProfile) -> Organization:
    """
    Create a tenant and everything it needs to be usable:

        organization -> primary business type -> default roles -> subscription
        -> Owner membership -> document numbering + shared default templates

    `profile` carries the user's own country, currency, timezone and state
    (validated against `reference_data` — see `_validate_profile`), so nothing
    here defaults or overrides them. Raises ImproperlyConfigured (a 500, by
    design) when platform data such as the default plan or default templates
    hasn't been seeded, and OrganizationProfileInvalid (a 400) when a
    reference-data field doesn't check out.
    """
    _validate_profile(profile)

    organization = Organization.objects.create(created_by=user, **asdict(profile))

    OrganizationBusinessType.objects.create(
        organization=organization,
        business_type=business_type,
        is_primary=True,
    )

    roles = seed_default_roles(organization)
    start_subscription(organization)

    membership = Membership.objects.create(
        user=user,
        organization=organization,
        role=roles[OWNER_ROLE_SLUG],
        accepted=True,
    )
    OwnerMembershipDetail.objects.create(membership=membership)

    provision_organization_documents(organization)
    return organization


def seed_default_roles(organization: Organization) -> dict[str, Role]:
    """Create the PRD's default system roles for one organization, with their permissions."""
    permission_ids: dict[str, int] = dict(Permission.objects.values_list("code", "pk"))

    roles = Role.objects.bulk_create(
        [Role(organization=organization, name=name, slug=slug, is_system=True) for slug, name in DEFAULT_ROLES]
    )

    links: list[RolePermission] = []
    for role in roles:
        grant = DEFAULT_ROLE_PERMISSIONS.get(role.slug, [])
        codes = sorted(permission_ids) if grant == ALL else list(grant)

        unknown = set(codes) - permission_ids.keys()
        if unknown:
            raise ImproperlyConfigured(
                f"DEFAULT_ROLE_PERMISSIONS['{role.slug}'] references unknown permission codes: {sorted(unknown)}"
            )
        links.extend(RolePermission(role=role, permission_id=permission_ids[code]) for code in codes)

    RolePermission.objects.bulk_create(links)
    return {role.slug: role for role in roles}


@transaction.atomic
def deactivate_organization(*, organization: Organization, actor) -> Organization:
    """
    Soft-deactivate a tenant (PRD §44: business records are never hard-deleted).
    Deactivated organizations drop out of `Organization.objects.for_user`, so
    members lose API access; reactivation is a support action in the admin.
    Idempotent.
    """
    if organization.is_active:
        organization.is_active = False
        organization.save(update_fields=["is_active", "updated_at"])
        # audit.record(actor=actor, action="organization.deactivated", ...)
        # subscriptions.services.pause_subscription(organization)   # if billing should stop
    return organization


# ---------------------------------------------------------------------------
# Members & invitations
# ---------------------------------------------------------------------------
def manager_role_slug(user, organization: Organization) -> str | None:
    """The built-in manager role (owner / administrator) the user holds in `organization`, if any."""
    if not getattr(user, "is_authenticated", False):
        return None
    return (
        Membership.objects.effective()
        .for_user(user)
        .for_organization(organization)
        .with_system_role(*MANAGER_ROLE_SLUGS)
        .values_list("role__slug", flat=True)
        .first()
    )


def _assert_role_assignable(actor_slug: str, role: Role, organization: Organization) -> None:
    """
    Who may hand out which role. Policy lives here, in one place:
      * a role must belong to the organization it is assigned in (tenant boundary);
      * nobody is made Owner by invitation or edit;
      * only the Owner can grant Administrator (no privilege escalation by admins).
    """
    if role.organization_id != organization.pk:
        raise MembershipRuleViolation("This role does not belong to the organization.", field="role")
    if role.is_system and role.slug == OWNER_ROLE_SLUG:
        raise MembershipRuleViolation("The Owner role cannot be assigned to another member.", field="role")
    if role.is_system and role.slug == ADMINISTRATOR_ROLE_SLUG and actor_slug != OWNER_ROLE_SLUG:
        raise MembershipRuleViolation("Only the Owner can grant the Administrator role.", field="role")


def _ensure_seat_available(organization: Organization) -> None:
    limit = get_plan_limit(organization, "max_users")
    if limit is None:
        return
    if Membership.objects.for_organization(organization).occupying_seat().count() >= limit:
        raise PlanLimitReached(f"Your plan allows up to {limit} users. Upgrade your plan to add more.")


@transaction.atomic
def invite_member(*, organization: Organization, invited_by, email: str, role: Role) -> Membership:
    """
    Invite `email` to `organization` with `role`.

    An unexpired invitation to the same email is rejected; an expired one is
    refreshed (new token, new expiry) instead of piling up duplicates.
    """
    if not organization.is_active:
        raise MembershipRuleViolation("This organization is deactivated.", field="organization")

    actor_slug = manager_role_slug(invited_by, organization)
    if actor_slug is None:
        raise NotAuthorized("Only organization Administrators and Owners can invite members.")

    _assert_role_assignable(actor_slug, role, organization)
    email = email.strip().lower()

    # Serialize invitations per organization so two concurrent requests can't both take the last seat.
    Organization.objects.select_for_update().get(pk=organization.pk)

    if Membership.objects.for_organization(organization).filter(accepted=True, user__email__iexact=email).exists():
        raise MembershipRuleViolation("This person is already a member of the organization.", field="invited_email")

    pending = (
        Membership.objects.for_organization(organization)
        .filter(accepted=False, invited_email__iexact=email)
        .first()
    )
    if pending and pending.expires_at > timezone.now():
        raise MembershipRuleViolation("This email is already invited to the organization.", field="invited_email")

    _ensure_seat_available(organization)  # an expired invite holds no seat, so a refresh needs one

    if pending:
        pending.role = role
        pending.invite_token = uuid.uuid4()
        pending.expires_at = default_invite_expiry()
        pending.is_active = True
        pending.save(update_fields=["role", "invite_token", "expires_at", "is_active", "updated_at"])
        membership = pending
    else:
        membership = Membership.objects.create(
            organization=organization,
            role=role,
            invited_email=email,
            accepted=False,
        )

    # Only email if the invitation really committed.
    transaction.on_commit(lambda: send_invitation_email(membership))
    # audit.record(actor=invited_by, action="membership.invited", ...)
    return membership


def _get_open_invitation(token, *, lock: bool = False) -> Membership:
    """The pending, unexpired invitation for `token` on an active organization, or InvitationInvalid."""
    queryset = Membership.objects.select_related("organization", "role")
    if lock:
        queryset = queryset.select_for_update(of=("self",))
    try:
        membership = queryset.get(invite_token=token, accepted=False)
    except Membership.DoesNotExist:
        raise InvitationInvalid("Invalid or expired invitation token.") from None

    if membership.expires_at < timezone.now():
        raise InvitationInvalid("This invitation has expired.")
    if not membership.organization.is_active:
        raise InvitationInvalid("This organization is no longer active.")
    return membership


def get_invitation_preview(token) -> Membership:
    """
    What the signup/login screen may show before the invitee authenticates:
    organization, role and invited email. Possession of the token (a random
    UUID) is the only credential, so callers must expose nothing beyond that.
    """
    return _get_open_invitation(token)


@transaction.atomic
def accept_invitation(*, user, token) -> Membership:
    """
    Bind an invitation to the authenticated user.

    The token alone isn't enough: the user must be signed in with a VERIFIED
    email that matches the invited address, otherwise anyone who got hold of
    the link (or registered with someone else's address) could join the tenant.
    """
    membership = _get_open_invitation(token, lock=True)

    if not user.is_verified:
        raise InvitationInvalid("Verify your email address before accepting an invitation.")
    if (membership.invited_email or "").lower() != user.email.lower():
        raise InvitationInvalid("This invitation was not sent to your email address.")
    if Membership.objects.filter(organization=membership.organization, user=user).exists():
        raise InvitationInvalid("You are already a member of this organization.")

    membership.user = user
    membership.accepted = True
    membership.is_active = True
    membership.save(update_fields=["user", "accepted", "is_active", "updated_at"])
    # audit.record(actor=user, action="membership.accepted", ...)
    return membership


def _lock_manageable_membership(membership: Membership, actor) -> tuple[Membership, str]:
    """Lock the row and enforce who may modify whom. Returns (membership, actor's manager slug)."""
    membership = (
        Membership.objects.select_for_update(of=("self",))
        .select_related("role", "organization")
        .get(pk=membership.pk)
    )

    actor_slug = manager_role_slug(actor, membership.organization)
    if actor_slug is None:
        raise NotAuthorized("Only organization Administrators and Owners can manage members.")
    if membership.user_id == actor.pk:
        raise MembershipRuleViolation("You cannot change your own membership.")

    target_slug = membership.role.slug if membership.role.is_system else None
    if target_slug == OWNER_ROLE_SLUG:
        raise MembershipRuleViolation("The Owner's membership cannot be changed here.")
    if target_slug == ADMINISTRATOR_ROLE_SLUG and actor_slug != OWNER_ROLE_SLUG:
        raise MembershipRuleViolation("Only the Owner can change an Administrator's membership.")
    return membership, actor_slug


@transaction.atomic
def update_membership(*, membership: Membership, actor, role: Role | None = None, is_active: bool | None = None) -> Membership:
    """Change a member's role and/or active flag."""
    membership, actor_slug = _lock_manageable_membership(membership, actor)

    if role is not None and role.pk != membership.role_id:
        _assert_role_assignable(actor_slug, role, membership.organization)
        membership.role = role
    if is_active is not None:
        membership.is_active = is_active

    membership.save(update_fields=["role", "is_active", "updated_at"])
    # audit.record(actor=actor, action="membership.updated", previous/new values)
    return membership


@transaction.atomic
def remove_membership(*, membership: Membership, actor) -> None:
    """
    Revoke a pending invitation (deleted), or remove an accepted member
    (deactivated, not deleted, so history and audit references stay intact).
    """
    membership, _ = _lock_manageable_membership(membership, actor)

    if membership.accepted:
        membership.is_active = False
        membership.save(update_fields=["is_active", "updated_at"])
    else:
        membership.delete()
    # audit.record(actor=actor, action="membership.removed", ...)


# ---------------------------------------------------------------------------
# Business types (edit flow: one now, several later)
# ---------------------------------------------------------------------------
@transaction.atomic
def set_business_types(
    *,
    organization: Organization,
    business_types: Iterable[BusinessType],
    primary: BusinessType,
) -> Organization:
    """
    Replace the organization's business types with `business_types`, marking
    `primary` as the primary one. Idempotent: sending the same payload twice
    changes nothing.
    """
    wanted = {bt.pk: bt for bt in business_types}
    if not wanted:
        raise BusinessTypeSelectionError("An organization must have at least one business type.")
    if primary.pk not in wanted:
        raise BusinessTypeSelectionError("The primary business type must be one of the selected types.")

    # Serialize concurrent edits of the same organization.
    Organization.objects.select_for_update().get(pk=organization.pk)

    links = OrganizationBusinessType.objects.for_organization(organization)
    links.exclude(business_type_id__in=wanted).delete()

    # Clear the flag first: the partial unique constraint is checked per statement.
    links.filter(is_primary=True).update(is_primary=False)

    existing = set(links.values_list("business_type_id", flat=True))
    OrganizationBusinessType.objects.bulk_create(
        [
            OrganizationBusinessType(organization=organization, business_type=bt)
            for pk, bt in wanted.items()
            if pk not in existing
        ]
    )
    links.filter(business_type_id=primary.pk).update(is_primary=True)

    # audit.record(...)  # organization.business_types_changed (previous/new values)
    return organization

def sync_permission_catalog(using=None):
    """Idempotent: creates missing codes, refreshes names/modules. Never deletes."""
    for code in PermissionCode:
        Permission.objects.using(using).update_or_create(
            code=code.value,
            defaults={
                "name": code.value.replace("_", " ").title(),
                "module": MODULE_BY_CODE[code],
            },
        )



def get_seat_limit(organization) -> int | None:
    """
    The plan's `max_users`; None means unlimited.

    """
    subscription = getattr(organization, "subscription", None)
    plan = getattr(subscription, "plan", None)
    return getattr(plan, "max_users", None)


def seats_in_use(organization) -> int:
    """Active members plus live (pending, unexpired) invitations."""
    members = Membership.objects.for_organization(organization).effective().count()
    invitations = Invitation.objects.for_organization(organization).pending().count()
    return members + invitations
