from rest_framework import permissions
from core.helper.enums import UsersRole



class IsOrganizationMember(permissions.BasePermission):
    """
    Grants access if the user is an active member of the organization.
    """

    def has_object_permission(self, request, view, obj):
        organization = getattr(obj, "organization", obj)
        return organization.memberships.filter(
            user=request.user, is_active=True
        ).exists()


class IsOrganizationAdminOrOwner(permissions.BasePermission):
    """
    Grants access if the user is an active Admin or Owner of the organization.
    """

    def has_object_permission(self, request, view, obj):
        organization = getattr(obj, "organization", obj)
        return organization.memberships.filter(
            user=request.user,
            is_active=True,
            role__in=[UsersRole.ADMIN, UsersRole.OWNER],
        ).exists()


class IsOrganizationOwner(permissions.BasePermission):
    """
    Grants access only to Organization Owners.
    Used for sensitive actions like plan upgrades or deactivation.
    """

    def has_object_permission(self, request, view, obj):
        organization = getattr(obj, "organization", obj)
        return organization.memberships.filter(
            user=request.user, is_active=True, role=UsersRole.OWNER
        ).exists()
