from __future__ import annotations

from core.applications.users.permissions import BaseTenantPermission
from core.helper.enums import PermissionCode


class CustomerPermission(BaseTenantPermission):
    """
    Action-level and object-level permission enforcement for Customer endpoints.
    Enforces tenant boundaries and granular capabilities:
      - list / retrieve / sales: VIEW_CUSTOMERS
      - create / update / partial_update / destroy: MANAGE_CUSTOMERS
      - adjust_credit: MANAGE_CUSTOMER_CREDIT
    """

    action_permissions: dict[str, str] = {
        "list": PermissionCode.VIEW_CUSTOMERS,
        "retrieve": PermissionCode.VIEW_CUSTOMERS,
        "sales": PermissionCode.VIEW_CUSTOMERS,
        "create": PermissionCode.MANAGE_CUSTOMERS,
        "update": PermissionCode.MANAGE_CUSTOMERS,
        "partial_update": PermissionCode.MANAGE_CUSTOMERS,
        "destroy": PermissionCode.MANAGE_CUSTOMERS,
        "adjust_credit": PermissionCode.MANAGE_CUSTOMER_CREDIT,
    }
    default_permission: str = PermissionCode.VIEW_CUSTOMERS
