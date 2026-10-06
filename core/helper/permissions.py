from rest_framework import permissions

from core.helper.enums import UsersRole


class IsOrganizationMember(permissions.BasePermission):
    """
    Allows access to users who are active, accepted members
    of the organization associated with the target object.
    """

    def has_object_permission(self, request, view, obj):
        organization = getattr(obj, "organization", obj)

        return organization.memberships.filter(
            user=request.user,
            is_active=True,
        ).exists()


class IsOrganizationAdminOrOwner(permissions.BasePermission):
    """
    Allows access to users who are active, accepted Admins or Owners
    of the organization associated with the target object.
    """

    def has_object_permission(self, request, view, obj):
        organization = getattr(obj, "organization", obj)

        return organization.memberships.filter(
            user=request.user,
            is_active=True,
            role__slug__in=[
                UsersRole.ADMIN,
                UsersRole.OWNER,
            ],
        ).exists()


class IsOrganizationOwner(permissions.BasePermission):
    """
    Allows access only to active, accepted Owners of the organization
    associated with the target object.

    Intended for sensitive organization-level operations such as
    subscription changes, organization deactivation, and other
    owner-only actions.
    """

    def has_object_permission(self, request, view, obj):
        organization = getattr(obj, "organization", obj)

        return organization.memberships.filter(
            user=request.user,
            is_active=True,
            role__slug=UsersRole.OWNER,
        ).exists()
