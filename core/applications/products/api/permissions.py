from __future__ import annotations

from typing import Any

from rest_framework.permissions import BasePermission
from rest_framework.request import Request
from rest_framework.views import APIView

from core.applications.users.models import Membership, Organization
from core.applications.users.services import has_org_permission
from core.helper.enums import PermissionCode


class ProductPermission(BasePermission):
    """
    Action-level and object-level permission enforcement for Product endpoints.
    Enforces tenant isolation and granular capabilities:
      - list: VIEW_PRODUCTS
      - retrieve: VIEW_PRODUCTS
      - create: CREATE_PRODUCT
      - update / partial_update: EDIT_PRODUCT
      - destroy: ARCHIVE_PRODUCT
    """

    action_permissions: dict[str, str] = {
        "list": PermissionCode.VIEW_PRODUCTS,
        "retrieve": PermissionCode.VIEW_PRODUCTS,
        "create": PermissionCode.CREATE_PRODUCT,
        "update": PermissionCode.EDIT_PRODUCT,
        "partial_update": PermissionCode.EDIT_PRODUCT,
        "destroy": PermissionCode.ARCHIVE_PRODUCT,
    }

    def _resolve_organization(self, request: Request, view: APIView, obj: Any = None) -> Organization | None:
        if obj is not None and hasattr(obj, "organization"):
            return obj.organization

        org_id = None
        if hasattr(request, "data") and isinstance(request.data, dict):
            org_id = request.data.get("organization")

        if not org_id:
            org_id = request.query_params.get("organization")

        if not org_id:
            org_id = request.headers.get("X-Organization-ID")

        if org_id:
            if isinstance(org_id, Organization):
                return org_id
            try:
                return Organization.objects.filter(pk=org_id, is_active=True).first()
            except (ValueError, TypeError):
                return None

        return None

    def has_permission(self, request: Request, view: APIView) -> bool:
        if not (request.user and request.user.is_authenticated):
            return False

        required_code = self.action_permissions.get(view.action, PermissionCode.VIEW_PRODUCTS)

        if getattr(request.user, "is_superuser", False):
            return True

        if view.action == "create":
            org = self._resolve_organization(request, view)
            if not org:
                # If organization ID is missing or invalid, let serializer validation raise standard 400
                return True
            return has_org_permission(request.user, org, required_code)

        if view.action == "list":
            org = self._resolve_organization(request, view)
            if org:
                return has_org_permission(request.user, org, required_code)
            # Default list: user must hold VIEW_PRODUCTS in at least one active membership
            return (
                Membership.objects.effective()
                .for_user(request.user)
                .granting(required_code)
                .exists()
            )

        return True

    def has_object_permission(self, request: Request, view: APIView, obj: Any) -> bool:
        if not (request.user and request.user.is_authenticated):
            return False

        required_code = self.action_permissions.get(view.action, PermissionCode.VIEW_PRODUCTS)
        return has_org_permission(request.user, obj.organization, required_code)
