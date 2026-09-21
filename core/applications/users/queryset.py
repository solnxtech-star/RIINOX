from __future__ import annotations

import auto_prefetch
from django.db.models import Exists
from django.db.models import OuterRef
from django.db.models import Prefetch
from django.db.models import Q
from django.utils import timezone

from core.applications.users.defaults import MANAGER_ROLE_SLUGS
from core.applications.users.defaults import OWNER_ROLE_SLUG


class BusinessTypeQuerySet(auto_prefetch.QuerySet):
    def active(self):
        """Business types that are active and can be used in organizations."""
        return self.filter(is_active=True)

    def for_onboarding(self):
        """The cards shown on the onboarding screen."""
        return self.active().filter(show_in_onboarding=True)

    def ordered(self):
        """Sort by the admin-defined sort_order, then name."""
        return self.order_by("sort_order", "name")


class OrganizationBusinessTypeQuerySet(auto_prefetch.QuerySet):
    def for_organization(self, organization):
        return self.filter(organization=organization)

    def with_business_type(self):
        return self.select_related("business_type")

    def primary_first(self):
        return self.order_by("-is_primary", "business_type__sort_order")


# ---------------------------------------------------------------------------
# Memberships
# ---------------------------------------------------------------------------
class MembershipQuerySet(auto_prefetch.QuerySet):
    def effective(self):
        """Accepted and active: the only memberships that grant anything."""
        return self.filter(accepted=True, is_active=True)

    def for_user(self, user):
        return self.filter(user=user)

    def for_organization(self, organization):
        return self.filter(organization=organization)

    def with_system_role(self, *role_slugs: str):
        """Memberships held through one of the built-in (is_system) roles, e.g. owner/administrator."""
        return self.filter(role__is_system=True, role__slug__in=role_slugs)

    def with_user_and_role(self):
        return self.select_related("user", "role")

    def occupying_seat(self):
        """Accepted, active members plus invitations that haven't expired: what counts against `max_users`."""
        return self.filter(Q(accepted=True, is_active=True) | Q(accepted=False, expires_at__gt=timezone.now()))

    def visible_to(self, user):
        """
        Memberships a user may see: their own, plus every membership of an
        organization they manage (Owner/Administrator). This is the tenant
        boundary for the membership endpoints.
        """
        if not getattr(user, "is_authenticated", False):
            return self.none()

        manages_org = self.model.objects.effective().filter(
            user=user,
            role__is_system=True,
            role__slug__in=MANAGER_ROLE_SLUGS,
            organization=OuterRef("organization"),
        )
        return self.filter(organization__is_active=True).filter(Q(user=user) | Exists(manages_org))

    def granting(self, permission_code: str):
        """
        Memberships whose role grants `permission_code`. The system Owner role
        always qualifies, so an Owner is never locked out if the permission
        catalog gains codes the role hasn't been synced with yet.
        """
        from core.applications.users.models import RolePermission

        grants = RolePermission.objects.filter(role=OuterRef("role"), permission__code=permission_code)
        is_owner = Q(role__slug=OWNER_ROLE_SLUG, role__is_system=True)
        return self.filter(is_owner | Exists(grants))


# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------
class RoleQuerySet(auto_prefetch.QuerySet):
    def for_organization(self, organization):
        return self.filter(organization=organization)

    def ordered(self):
        return self.order_by("-is_system", "name")


# ---------------------------------------------------------------------------
# Organizations
# ---------------------------------------------------------------------------
class OrganizationQuerySet(auto_prefetch.QuerySet):
    def active(self):
        return self.filter(is_active=True)

    def for_user(self, user):
        """
        THE tenant boundary for organization reads: active organizations the
        user is an accepted, active member of. Every user-facing organization
        query starts here.
        """
        if not getattr(user, "is_authenticated", False):
            return self.none()

        from core.applications.users.models import Membership

        is_member = Membership.objects.filter(
            organization=OuterRef("pk"),
            user=user,
            accepted=True,
            is_active=True,
        )
        return self.active().filter(Exists(is_member))

    def with_plan(self):
        return self.select_related("subscription__plan")

    def with_business_types(self):
        from core.applications.users.models import OrganizationBusinessType

        links = OrganizationBusinessType.objects.with_business_type().primary_first()
        return self.prefetch_related(Prefetch("organization_business_types", queryset=links))

    def with_detail(self):
        """Everything OrganizationSerializer reads: a fixed number of queries per page."""
        return self.with_plan().with_business_types()


# ---------------------------------------------------------------------------
# Managers (used as `objects = ...Manager()` on the models)
# ---------------------------------------------------------------------------
BusinessTypeManager = auto_prefetch.Manager.from_queryset(BusinessTypeQuerySet)
OrganizationBusinessTypeManager = auto_prefetch.Manager.from_queryset(OrganizationBusinessTypeQuerySet)
MembershipManager = auto_prefetch.Manager.from_queryset(MembershipQuerySet)
RoleManager = auto_prefetch.Manager.from_queryset(RoleQuerySet)
OrganizationManager = auto_prefetch.Manager.from_queryset(OrganizationQuerySet)
