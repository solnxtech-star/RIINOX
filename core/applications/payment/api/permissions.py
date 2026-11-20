from __future__ import annotations

from rest_framework.exceptions import NotFound
from rest_framework.permissions import BasePermission

from core.applications.users import services
from core.applications.users.permissions import resolve_active_membership
from core.helper.enums import PermissionCode

# Reading: VIEW_PAYMENTS or VIEW_RECEIPTS. Each payment *is* the customer's
# receipt, and some roles (Front Desk) hold only VIEW_RECEIPTS.
READ_CODES = (PermissionCode.VIEW_PAYMENTS, PermissionCode.VIEW_RECEIPTS)
WRITE_CODES = (PermissionCode.RECORD_PAYMENT,)


class PaymentPermission(BasePermission):
    message = "Your role does not allow this action in this organization."

    def has_permission(self, request, view) -> bool:
        user = request.user
        if not (user and user.is_authenticated):
            return False
        if getattr(user, "is_superuser", False):
            return True
        membership = resolve_active_membership(request, view)
        if membership is None:
            raise NotFound("Organization not found.")
        codes = WRITE_CODES if getattr(view, "action", None) == "create" else READ_CODES
        return any(services.has_org_permission(user, membership.organization, code) for code in codes)
