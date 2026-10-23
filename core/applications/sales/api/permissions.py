from __future__ import annotations

from typing import Any

from rest_framework.request import Request

from core.applications.users.models import Organization
from core.applications.users.permissions import BaseTenantPermission
from core.helper.enums import PermissionCode


class SalePermission(BaseTenantPermission):
    """
    Action-level and object-level permission enforcement for Sale endpoints.
    Enforces tenant boundaries and granular capabilities:
      - list / retrieve: VIEW_SALES
      - create / update: CREATE_SALE
    """

    action_permissions: dict[str, str] = {
        "list": PermissionCode.VIEW_SALES,
        "retrieve": PermissionCode.VIEW_SALES,
        "create": PermissionCode.CREATE_SALE,
        "update": PermissionCode.CREATE_SALE,
        "partial_update": PermissionCode.CREATE_SALE,
        "destroy": PermissionCode.CREATE_SALE,
        "cancel": PermissionCode.CREATE_SALE,
    }
    default_permission: str = PermissionCode.VIEW_SALES


class SaleReturnPermission(BaseTenantPermission):
    """
    Action-level and object-level permission enforcement for SaleReturn endpoints.
    Enforces tenant boundaries and granular capabilities:
      - list / retrieve: VIEW_SALES
      - create / update: CREATE_SALE_RETURN
    """

    action_permissions: dict[str, str] = {
        "list": PermissionCode.VIEW_SALES,
        "retrieve": PermissionCode.VIEW_SALES,
        "create": PermissionCode.CREATE_SALE_RETURN,
        "update": PermissionCode.CREATE_SALE_RETURN,
        "partial_update": PermissionCode.CREATE_SALE_RETURN,
        "destroy": PermissionCode.CREATE_SALE_RETURN,
    }
    default_permission: str = PermissionCode.VIEW_SALES

    def _resolve_organization(self, request: Request, view: Any = None, obj: Any = None) -> Organization | None:
        org = super()._resolve_organization(request, view=view, obj=obj)
        if org:
            return org

        if hasattr(request, "data") and hasattr(request.data, "get") and "original_sale" in request.data:
            from core.applications.sales.models import Sale
            original_sale = Sale.objects.filter(pk=request.data["original_sale"]).first()
            if original_sale:
                return original_sale.organization

        return None

