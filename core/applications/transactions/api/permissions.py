from __future__ import annotations

from core.applications.users.permissions import BaseTenantPermission
from core.helper.enums import PermissionCode


class TransactionPermission(BaseTenantPermission):
    """
    Transactions are read-only (see TransactionViewSet), so only read actions
    exist: list / retrieve need VIEW_FINANCIAL_REPORTS.
    """

    action_permissions: dict[str, str] = {
        "list": PermissionCode.VIEW_FINANCIAL_REPORTS,
        "retrieve": PermissionCode.VIEW_FINANCIAL_REPORTS,
    }
    default_permission: str = PermissionCode.VIEW_FINANCIAL_REPORTS
