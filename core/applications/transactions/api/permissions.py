from __future__ import annotations

from core.applications.users.permissions import BaseTenantPermission
from core.helper.enums import PermissionCode


class CustomerPermission(BaseTenantPermission):
    """
    Action-level and object-level permission enforcement for Customer endpoints.
    Enforces tenant boundaries and granular capabilities:
      - list / retrieve: VIEW_CUSTOMERS
      - create / update / destroy: MANAGE_CUSTOMERS
    """

    action_permissions: dict[str, str] = {
        "list": PermissionCode.VIEW_CUSTOMERS,
        "retrieve": PermissionCode.VIEW_CUSTOMERS,
        "create": PermissionCode.MANAGE_CUSTOMERS,
        "update": PermissionCode.MANAGE_CUSTOMERS,
        "partial_update": PermissionCode.MANAGE_CUSTOMERS,
        "destroy": PermissionCode.MANAGE_CUSTOMERS,
    }
    default_permission: str = PermissionCode.VIEW_CUSTOMERS


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
