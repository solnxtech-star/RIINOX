"""
Last Modified: 2026-10-18
Module: core.applications.products.api.permissions
Description:
    RBAC permission enforcement for Product and Variant endpoints.
    Maps DRF viewset actions to granular system permissions (e.g., ARCHIVE_PRODUCT,
    EDIT_PRODUCT, VIEW_PRODUCTS) and guarantees strict tenant boundary validation.
"""

from __future__ import annotations

from core.applications.users.permissions import BaseTenantPermission
from core.helper.enums import PermissionCode


class ProductPermission(BaseTenantPermission):
    """
    Action-level and object-level permission enforcement for Product and Variant endpoints.
    Enforces tenant isolation and granular capabilities:
      - list: VIEW_PRODUCTS
      - retrieve: VIEW_PRODUCTS
      - create: CREATE_PRODUCT
      - update / partial_update: EDIT_PRODUCT
      - destroy / archive: ARCHIVE_PRODUCT (PRD §44)
      - restore: EDIT_PRODUCT (PRD §44)
    """

    action_permissions: dict[str, str] = {
        "list": PermissionCode.VIEW_PRODUCTS,
        "retrieve": PermissionCode.VIEW_PRODUCTS,
        "create": PermissionCode.CREATE_PRODUCT,
        "update": PermissionCode.EDIT_PRODUCT,
        "partial_update": PermissionCode.EDIT_PRODUCT,
        "destroy": PermissionCode.ARCHIVE_PRODUCT,
        "archive": PermissionCode.ARCHIVE_PRODUCT,
        "restore": PermissionCode.EDIT_PRODUCT,
    }
    default_permission: str = PermissionCode.VIEW_PRODUCTS


