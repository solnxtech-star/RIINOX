from rest_framework.exceptions import NotFound
from rest_framework.permissions import BasePermission

from core.applications.users import services
from core.applications.users.defaults import ADMINISTRATOR_ROLE_SLUG
from core.applications.users.defaults import MANAGER_ROLE_SLUGS
from core.applications.users.defaults import OWNER_ROLE_SLUG
from core.applications.users.models import Membership


class _HasSystemRole(BasePermission):
    role_slugs: tuple[str, ...] = ()
    message = "You do not have permission to perform this action in this organization."

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated)

    def has_object_permission(self, request, view, obj):
        # `obj` is the Organization.
        return services.has_org_role(request.user, obj, *self.role_slugs)


class IsOrganizationOwner(_HasSystemRole):
    role_slugs = (OWNER_ROLE_SLUG,)
    message = "Only the organization Owner can perform this action."


class IsOrganizationAdminOrOwner(_HasSystemRole):
    role_slugs = (OWNER_ROLE_SLUG, ADMINISTRATOR_ROLE_SLUG)
    message = "Only organization Administrators and Owners can perform this action."


class IsMembershipManager(BasePermission):
    """
    Object permission for changing or removing a membership: the caller must be
    an Owner/Administrator of the membership's organization. Reading is not
    gated here; the membership queryset (`visible_to`) already limits who can
    see a row. Finer rules (nobody edits the Owner, admins can't edit other
    admins, no self-edits) are enforced in services.
    """

    message = "Only organization Administrators and Owners can manage members."

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated)

    def has_object_permission(self, request, view, obj):
        return services.has_org_role(request.user, obj.organization, *MANAGER_ROLE_SLUGS)


def resolve_active_membership(request, view):
    """
    The caller's active membership in the organization named in the URL, or
    None. Resolved once per request. Non-members get a 404 (not a 403) so the
    API never reveals which organizations exist.
    """
    if hasattr(request, "_active_membership"):
        return request._active_membership

    membership = None
    org_id = view.kwargs.get("organization_id")
    if org_id and request.user and request.user.is_authenticated:
        membership = (
            Membership.objects.effective()
            .for_user(request.user)
            .filter(organization_id=org_id, organization__is_active=True)
            .select_related("organization", "role")
            .first()
        )
    request._active_membership = membership
    return membership


def HasOrgPermission(permission_code: str):
    """
    Permission-class factory: caller is an active member of the organization in
    the URL AND their role grants `permission_code` (Owner always qualifies).
    Usage: permission_classes = [HasOrgPermission(PermissionCode.INVITE_TEAM_MEMBER)]
    """

    class _HasOrgPermission(BasePermission):
        message = "Your role does not allow this action in this organization."

        def has_permission(self, request, view):
            membership = resolve_active_membership(request, view)
            if membership is None:
                raise NotFound("Organization not found.")
            return Membership.objects.filter(pk=membership.pk).granting(permission_code).exists()

    _HasOrgPermission.__name__ = f"HasOrgPermission_{permission_code}"
    return _HasOrgPermission


class OrganizationScopedMixin:
    """Gives views `self.membership` and `self.organization`, both from the verified membership."""

    @property
    def membership(self):
        return resolve_active_membership(self.request, self)

    @property
    def organization(self):
        return self.membership.organization
