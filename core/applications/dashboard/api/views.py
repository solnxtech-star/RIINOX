from __future__ import annotations

from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.applications.dashboard import services
from core.applications.dashboard.api.schemas import dashboard_schema
from core.applications.dashboard.period import resolve_period
from core.applications.users.permissions import BaseTenantPermission
from core.applications.users.permissions import OrganizationScopedMixin
from core.applications.users.services import has_org_permission
from core.applications.warehouse.access import parse_location_param
from core.applications.warehouse.models import Warehouse
from core.helper.enums import PermissionCode
from core.helper.org_time import organization_today
from core.helper.org_time import organization_tz

DEFAULT_LIMIT = 5
MAX_LIMIT = 20
DEFAULT_CURRENCY = "NGN"


class DashboardPermission(BaseTenantPermission):
    """Every section needs VIEW_DASHBOARD; finer rules (profit, audit) are applied per figure."""

    action_permissions: dict[str, str] = {}
    default_permission: str = PermissionCode.VIEW_DASHBOARD


@dashboard_schema
class DashboardViewSet(OrganizationScopedMixin, viewsets.ViewSet):
    permission_classes = [IsAuthenticated, DashboardPermission]

    def list(self, request, organization_id=None):
        """Every section in one response (first paint). Sections are also served on their own."""
        ctx = self._context()
        limit = _limit(request.query_params)
        return Response(
            {
                "meta": _meta(ctx),
                "overview": services.overview(ctx),
                "sales_chart": services.sales_chart(ctx),
                "alerts": services.alerts(ctx),
                "top_products": services.top_products(ctx, limit),
                "recent_activity": services.recent_activity(ctx, limit),
                "recent_transactions": services.recent_transactions(ctx, limit),
            }
        )

    @action(detail=False, methods=["get"])
    def overview(self, request, organization_id=None):
        ctx = self._context()
        return Response({"meta": _meta(ctx), **services.overview(ctx)})

    @action(detail=False, methods=["get"], url_path="sales-chart")
    def sales_chart(self, request, organization_id=None):
        ctx = self._context()
        return Response({"meta": _meta(ctx), **services.sales_chart(ctx)})

    @action(detail=False, methods=["get"])
    def alerts(self, request, organization_id=None):
        ctx = self._context()
        return Response({"meta": _meta(ctx), **services.alerts(ctx)})

    @action(detail=False, methods=["get"], url_path="top-products")
    def top_products(self, request, organization_id=None):
        ctx = self._context()
        return Response({"meta": _meta(ctx), **services.top_products(ctx, _limit(request.query_params))})

    @action(detail=False, methods=["get"])
    def activity(self, request, organization_id=None):
        ctx = self._context()
        return Response({"meta": _meta(ctx), **services.recent_activity(ctx, _limit(request.query_params))})

    @action(detail=False, methods=["get"], url_path="recent-transactions")
    def recent_transactions(self, request, organization_id=None):
        ctx = self._context()
        return Response({"meta": _meta(ctx), **services.recent_transactions(ctx, _limit(request.query_params))})

    # ------------------------------------------------------------------ #
    def _context(self) -> services.DashboardContext:
        organization, user = self.organization, self.request.user
        scope = self.location_scope

        location = None
        location_id = parse_location_param(self.request.query_params)
        if location_id is not None:
            location = Warehouse.objects.filter(organization=organization, pk=location_id).first()
            if location is None:
                raise ValidationError({"location": ["No such location in this organization."]})
            scope.require(location)

        def can(code):
            return user.is_superuser or has_org_permission(user, organization, code)

        return services.DashboardContext(
            organization=organization,
            scope=scope,
            location=location,
            period=resolve_period(self.request.query_params, organization),
            tz=organization_tz(organization),
            today=organization_today(organization),
            currency=(getattr(organization, "currency", "") or DEFAULT_CURRENCY).upper(),
            can_view_profit=can(PermissionCode.VIEW_DASHBOARD_PROFIT),
            can_view_audit=can(PermissionCode.VIEW_AUDIT_LOGS),
            can_approve_adjustments=can(PermissionCode.APPROVE_STOCK_ADJUSTMENT),
        )


def _meta(ctx: services.DashboardContext) -> dict:
    return {
        "period": ctx.period.as_dict(),
        "location": {"id": str(ctx.location.pk), "name": ctx.location.name} if ctx.location else None,
        "all_locations": ctx.location is None,
        "currency": ctx.currency,
        "timezone": str(ctx.tz),
        "has_profit_permission": ctx.can_view_profit,
    }


def _limit(params) -> int:
    raw = params.get("limit")
    if raw in (None, ""):
        return DEFAULT_LIMIT
    try:
        value = int(raw)
    except ValueError:
        raise ValidationError({"limit": [f"Must be a whole number from 1 to {MAX_LIMIT}."]}) from None
    if not 1 <= value <= MAX_LIMIT:
        raise ValidationError({"limit": [f"Must be a whole number from 1 to {MAX_LIMIT}."]})
    return value
