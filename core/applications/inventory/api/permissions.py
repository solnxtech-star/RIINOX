from __future__ import annotations

from core.applications.users.permissions import BaseTenantPermission
from core.helper.enums import PermissionCode


class StockAdjustmentPermission(BaseTenantPermission):
    action_permissions: dict[str, str] = {
        "list": PermissionCode.VIEW_INVENTORY,
        "retrieve": PermissionCode.VIEW_INVENTORY,
        "create": PermissionCode.REQUEST_STOCK_ADJUSTMENT,
        "approve": PermissionCode.APPROVE_STOCK_ADJUSTMENT,
    }
    default_permission: str = PermissionCode.VIEW_INVENTORY


class StockTransferPermission(BaseTenantPermission):
    action_permissions: dict[str, str] = {
        "execute": PermissionCode.TRANSFER_STOCK,
        "create": PermissionCode.TRANSFER_STOCK,
    }
    default_permission: str = PermissionCode.TRANSFER_STOCK


class InventoryPermission(BaseTenantPermission):
    action_permissions: dict[str, str] = {
        "list": PermissionCode.VIEW_INVENTORY,
        "retrieve": PermissionCode.VIEW_INVENTORY,
    }
    default_permission: str = PermissionCode.VIEW_INVENTORY


class InventoryLedgerPermission(BaseTenantPermission):
    action_permissions: dict[str, str] = {
        "list": PermissionCode.VIEW_INVENTORY,
        "retrieve": PermissionCode.VIEW_INVENTORY,
    }
    default_permission: str = PermissionCode.VIEW_INVENTORY
