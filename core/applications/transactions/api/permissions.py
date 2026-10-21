from __future__ import annotations

from core.applications.users.permissions import BaseTenantPermission
from core.helper.enums import PermissionCode




class TransactionPermission(BaseTenantPermission):
    """
    Action-level and object-level permission enforcement for Transaction endpoints.
    Enforces tenant boundaries and granular capabilities:
      - list / retrieve / create: VIEW_FINANCIAL_REPORTS
    """

    action_permissions: dict[str, str] = {
        "list": PermissionCode.VIEW_FINANCIAL_REPORTS,
        "retrieve": PermissionCode.VIEW_FINANCIAL_REPORTS,
        "create": PermissionCode.VIEW_FINANCIAL_REPORTS,
        "update": PermissionCode.VIEW_FINANCIAL_REPORTS,
        "partial_update": PermissionCode.VIEW_FINANCIAL_REPORTS,
        "destroy": PermissionCode.VIEW_FINANCIAL_REPORTS,
    }
    default_permission: str = PermissionCode.VIEW_FINANCIAL_REPORTS
