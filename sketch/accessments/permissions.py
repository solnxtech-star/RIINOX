from rest_framework.permissions import BasePermission, SAFE_METHODS
from typing import Optional

from core.applications.users.models import User

class IsPrincipalOrSchoolOwner(BasePermission):
    """
    Full access: School Owner, Principal.
    Read-only: Teachers.
    Denied: HR, Bursar, Vice-Principal, Other admins, students.
    """

    def has_permission(self, request, view):
        user = getattr(request, "user", None)

        if not user or not user.is_authenticated:
            return False

        # Teachers: READ-ONLY
        if user.role == "teacher":
            return request.method in SAFE_METHODS

        # Admins: only school_owner and principal
        if user.role == "admin":
            admin_profile = getattr(user, "adminprofile", None)
            if not admin_profile:
                return False
            return admin_profile.admin_type in ["school_owner", "principal"]

        # All other roles denied
        return False


class IsPrincipalOrSchoolOwnerForPolicies(BasePermission):
    """
    Principal / School Owner: full access
    Teachers: read-only (GET/HEAD/OPTIONS)
    Others: denied
    """

    def has_permission(self, request, view):
        user: Optional[User] = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return False

        # Teachers read-only
        if user.role == "teacher":
            return request.method in SAFE_METHODS

        # Admins: check adminprofile.admin_type
        if user.role == "admin":
            admin_profile = getattr(user, "adminprofile", None)
            if not admin_profile:
                return False
            return admin_profile.admin_type in ["school_owner", "principal"]

        return False
