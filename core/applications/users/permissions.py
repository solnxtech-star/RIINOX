from __future__ import annotations

from typing import Any

from rest_framework.exceptions import NotFound
from rest_framework.permissions import BasePermission
from rest_framework.request import Request

from core.applications.users import services
from core.applications.users.defaults import ADMINISTRATOR_ROLE_SLUG
from core.applications.users.defaults import MANAGER_ROLE_SLUGS
from core.applications.users.defaults import OWNER_ROLE_SLUG
from core.applications.users.models import Membership, Organization


class _HasSystemRole(BasePermission):
    role_slugs: tuple[str, ...] = ()
    message = "You do not have permission to perform this action in this organization."

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated)

    def has_object_permission(self, request, view, obj):
        # `obj` is the Organization.
        return services.has_org_role(request.user, obj, *self.role_slugs)


class IsOrganizationOwner(_HasSystemRole):
    role_slugs = (OWNER_ROLE_SLUG,)
    message = "Only the organization Owner can perform this action."


class IsOrganizationAdminOrOwner(_HasSystemRole):
    role_slugs = (OWNER_ROLE_SLUG, ADMINISTRATOR_ROLE_SLUG)
    message = "Only organization Administrators and Owners can perform this action."


class IsMembershipManager(BasePermission):
    """
    Object permission for changing or removing a membership: the caller must be
    an Owner/Administrator of the membership's organization. Reading is not
    gated here; the membership queryset (`visible_to`) already limits who can
    see a row. Finer rules (nobody edits the Owner, admins can't edit other
    admins, no self-edits) are enforced in services.
    """

    message = "Only organization Administrators and Owners can manage members."

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated)

    def has_object_permission(self, request, view, obj):
        return services.has_org_role(request.user, obj.organization, *MANAGER_ROLE_SLUGS)


def resolve_active_membership(request, view):
    """
    The caller's active membership in the organization named in the URL, or
    None. Resolved once per request. Non-members get a 404 (not a 403) so the
    API never reveals which organizations exist.
    """
    if hasattr(request, "_active_membership"):
        return request._active_membership

    membership = None
    org_id = view.kwargs.get("organization_id")
    if org_id and request.user and request.user.is_authenticated:
        membership = (
            Membership.objects.effective()
            .for_user(request.user)
            .filter(organization_id=org_id, organization__is_active=True)
            .select_related("organization", "role")
            .first()
        )
    request._active_membership = membership
    return membership


def HasOrgPermission(permission_code: str):
    """
    Permission-class factory: caller is an active member of the organization in
    the URL AND their role grants `permission_code` (Owner always qualifies).
    Usage: permission_classes = [HasOrgPermission(PermissionCode.INVITE_TEAM_MEMBER)]
    """

    class _HasOrgPermission(BasePermission):
        message = "Your role does not allow this action in this organization."

        def has_permission(self, request, view):
            membership = resolve_active_membership(request, view)
            if membership is None:
                raise NotFound("Organization not found.")
            return Membership.objects.filter(pk=membership.pk).granting(permission_code).exists()

    _HasOrgPermission.__name__ = f"HasOrgPermission_{permission_code}"
    return _HasOrgPermission


class OrganizationScopedMixin:
    """Gives views `self.membership` and `self.organization`, both from the verified membership."""

    @property
    def membership(self):
        return resolve_active_membership(self.request, self)

    @property
    def organization(self):
        mem = self.membership
        if mem:
            return mem.organization
        user = getattr(getattr(self, "request", None), "user", None)
        if user and getattr(user, "is_superuser", False):
            org_id = self.kwargs.get("organization_id") or self.kwargs.get("organization_pk")
            if org_id:
                return Organization.objects.filter(pk=org_id, is_active=True).first()
        return None


def resolve_request_organization(
    request: Request,
    view: Any = None,
    obj: Any = None,
) -> Organization | None:
    """
    Resolve the organization target for a given request, object, or view context.
    Evaluates in order:
      1. Explicit `obj.organization` (or `obj` if it is an Organization)
      2. Request body `organization` (e.g. on POST/PUT)
      3. Query parameter `organization` (e.g. on GET ?organization=<id>)
      4. Header `X-Organization-ID`
      5. View kwargs `organization_id` or `organization_pk` (nested routes)
    """
    if obj is not None:
        if hasattr(obj, "organization"):
            return obj.organization
        if isinstance(obj, Organization):
            return obj

    org_id = None
    if hasattr(request, "data") and hasattr(request.data, "get"):
        org_id = request.data.get("organization")

    if not org_id and hasattr(request, "query_params"):
        org_id = request.query_params.get("organization")

    if not org_id and hasattr(request, "headers"):
        org_id = request.headers.get("X-Organization-ID")

    if not org_id and view is not None and hasattr(view, "kwargs") and isinstance(view.kwargs, dict):
        org_id = view.kwargs.get("organization_id") or view.kwargs.get("organization_pk")

    if org_id:
        if isinstance(org_id, Organization):
            return org_id
        try:
            return Organization.objects.filter(pk=org_id, is_active=True).first()
        except (ValueError, TypeError):
            return None

    user = getattr(request, "user", None)
    if user and getattr(user, "is_authenticated", False):
        return getattr(user, "organization", None)

    return None


class BaseTenantPermission(BasePermission):
    """
    Base action-level and object-level permission enforcement for tenant-scoped resources.
    Subclasses define:
      - action_permissions: dict mapping action names ('list', 'create', etc.) to PermissionCode
      - default_permission: fallback PermissionCode for actions not explicitly mapped
    """

    action_permissions: dict[str, str] = {}
    default_permission: str | None = None

    def get_required_permission(self, view: Any) -> str | None:
        action = getattr(view, "action", None)
        if action and action in self.action_permissions:
            return self.action_permissions[action]
        return self.default_permission

    def _resolve_organization(self, request: Request, view: Any = None, obj: Any = None) -> Organization | None:
        return resolve_request_organization(request, view=view, obj=obj)

    def has_permission(self, request: Request, view: Any) -> bool:
        if not (request.user and request.user.is_authenticated):
            return False

        if getattr(request.user, "is_superuser", False):
            return True

        required_code = self.get_required_permission(view)
        if not required_code:
            return True

        # When organization is in the URL path, enforce membership and permission strictly
        org_in_url = None
        if view is not None and hasattr(view, "kwargs") and isinstance(view.kwargs, dict):
            org_in_url = view.kwargs.get("organization_id") or view.kwargs.get("organization_pk")

        if org_in_url:
            membership = resolve_active_membership(request, view)
            if membership is None:
                raise NotFound("Organization not found.")
            return services.has_org_permission(request.user, membership.organization, required_code)

        action = getattr(view, "action", None)

        if action == "create":
            org = self._resolve_organization(request, view=view)
            if not org:
                # If organization ID is missing or invalid, let serializer validation handle standard 400
                return True
            return services.has_org_permission(request.user, org, required_code)

        if action == "list":
            org = self._resolve_organization(request, view=view)
            if org:
                return services.has_org_permission(request.user, org, required_code)
            # Default list: user must hold the permission in at least one active membership
            return (
                Membership.objects.effective()
                .for_user(request.user)
                .granting(required_code)
                .exists()
            )

        return True

    def has_object_permission(self, request: Request, view: Any, obj: Any) -> bool:
        if not (request.user and request.user.is_authenticated):
            return False

        if getattr(request.user, "is_superuser", False):
            return True

        required_code = self.get_required_permission(view)
        if not required_code:
            return True

        org = getattr(obj, "organization", obj if isinstance(obj, Organization) else None)
        if org is None and hasattr(obj, "warehouse"):
            org = getattr(obj.warehouse, "organization", None)
        if org is None:
            return True

        return services.has_org_permission(request.user, org, required_code)
