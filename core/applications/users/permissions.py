from rest_framework.permissions import BasePermission

from core.applications.users import services
from core.applications.users.defaults import ADMINISTRATOR_ROLE_SLUG
from core.applications.users.defaults import MANAGER_ROLE_SLUGS
from core.applications.users.defaults import OWNER_ROLE_SLUG


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
