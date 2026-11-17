from __future__ import annotations

from core.applications.users.permissions import BaseTenantPermission
from core.helper.enums import PermissionCode


class ExpensePermission(BaseTenantPermission):
    action_permissions: dict[str, str] = {
        "list": PermissionCode.VIEW_EXPENSES,
        "retrieve": PermissionCode.VIEW_EXPENSES,
        "create": PermissionCode.RECORD_EXPENSE,
        "partial_update": PermissionCode.RECORD_EXPENSE,
        "void": PermissionCode.VOID_EXPENSE,
    }
    default_permission: str = PermissionCode.VIEW_EXPENSES
