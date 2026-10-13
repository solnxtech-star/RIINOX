from __future__ import annotations

from core.applications.users.permissions import BaseTenantPermission
from core.helper.enums import PermissionCode


class WarehousePermission(BaseTenantPermission):
    """
    Action-level and object-level permission enforcement for Warehouse endpoints.
    Enforces tenant boundaries and granular capabilities:
      - list / retrieve: VIEW_INVENTORY
      - create / update / destroy: MANAGE_LOCATIONS
    """

    action_permissions: dict[str, str] = {
        "list": PermissionCode.VIEW_INVENTORY,
        "retrieve": PermissionCode.VIEW_INVENTORY,
        "create": PermissionCode.MANAGE_LOCATIONS,
        "update": PermissionCode.MANAGE_LOCATIONS,
        "partial_update": PermissionCode.MANAGE_LOCATIONS,
        "destroy": PermissionCode.MANAGE_LOCATIONS,
    }
    default_permission: str = PermissionCode.VIEW_INVENTORY


class StockLocationPermission(BaseTenantPermission):
    """
    Action-level and object-level permission enforcement for Stock Location endpoints.
    Enforces tenant boundaries and granular capabilities:
      - list / retrieve: VIEW_INVENTORY
      - create / update / destroy: MANAGE_LOCATIONS
    """

    action_permissions: dict[str, str] = {
        "list": PermissionCode.VIEW_INVENTORY,
        "retrieve": PermissionCode.VIEW_INVENTORY,
        "create": PermissionCode.MANAGE_LOCATIONS,
        "update": PermissionCode.MANAGE_LOCATIONS,
        "partial_update": PermissionCode.MANAGE_LOCATIONS,
        "destroy": PermissionCode.MANAGE_LOCATIONS,
    }
    default_permission: str = PermissionCode.VIEW_INVENTORY
