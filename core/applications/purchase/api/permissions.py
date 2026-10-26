from __future__ import annotations

from typing import Any

from core.applications.users.permissions import BaseTenantPermission
from core.helper.enums import PermissionCode


class PurchasePermission(BaseTenantPermission):
    """
    Action-level and object-level permission enforcement for Purchase endpoints.
    Enforces tenant boundaries and granular capabilities:
      - list / retrieve / summary / export / stock_activity: VIEW_PURCHASES
      - create / update / partial_update / destroy / cancel: CREATE_PURCHASE
      - receive: RECEIVE_GOODS
      - purchase_invoice / payments / returns:
        * GET -> VIEW_PURCHASES
        * POST -> CREATE_PURCHASE
    """

    action_permissions: dict[str, str] = {
        "list": PermissionCode.VIEW_PURCHASES,
        "retrieve": PermissionCode.VIEW_PURCHASES,
        "summary": PermissionCode.VIEW_PURCHASES,
        "export": PermissionCode.VIEW_PURCHASES,
        "stock_activity": PermissionCode.VIEW_PURCHASES,
        "create": PermissionCode.CREATE_PURCHASE,
        "update": PermissionCode.CREATE_PURCHASE,
        "partial_update": PermissionCode.CREATE_PURCHASE,
        "destroy": PermissionCode.CREATE_PURCHASE,
        "cancel": PermissionCode.CREATE_PURCHASE,
        "receive": PermissionCode.RECEIVE_GOODS,
        "purchase_invoice": PermissionCode.CREATE_PURCHASE,
        "payments": PermissionCode.CREATE_PURCHASE,
        "returns": PermissionCode.CREATE_PURCHASE,
    }
    default_permission: str = PermissionCode.VIEW_PURCHASES

    def get_required_permission(self, view: Any) -> str | None:
        action = getattr(view, "action", None)
        request = getattr(view, "request", None)
        method = getattr(request, "method", "GET") if request is not None else "GET"

        if action in ("purchase_invoice", "payments", "returns") and method == "GET":
            return PermissionCode.VIEW_PURCHASES

        return super().get_required_permission(view)
