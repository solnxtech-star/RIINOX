from __future__ import annotations

from core.applications.users.permissions import BaseTenantPermission
from core.helper.enums import PermissionCode


class PurchasePermission(BaseTenantPermission):
    """
    Action-level and object-level permission enforcement for Purchase endpoints.
    Enforces tenant boundaries and granular capabilities:
      - list / retrieve: VIEW_PURCHASES
      - create / update / destroy: CREATE_PURCHASE
      - receive: RECEIVE_GOODS
    """

    action_permissions: dict[str, str] = {
        "list": PermissionCode.VIEW_PURCHASES,
        "retrieve": PermissionCode.VIEW_PURCHASES,
        "create": PermissionCode.CREATE_PURCHASE,
        "update": PermissionCode.CREATE_PURCHASE,
        "partial_update": PermissionCode.CREATE_PURCHASE,
        "destroy": PermissionCode.CREATE_PURCHASE,
        "receive": PermissionCode.RECEIVE_GOODS,
    }
    default_permission: str = PermissionCode.VIEW_PURCHASES

