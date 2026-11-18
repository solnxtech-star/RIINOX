from __future__ import annotations

from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.applications.dashboard.period import resolve_period
from core.applications.report import exports
from core.applications.report import services
from core.applications.report.api.schemas import report_viewset_schema
from core.applications.users.permissions import BaseTenantPermission
from core.applications.users.permissions import OrganizationScopedMixin
from core.applications.users.permissions import resolve_active_membership
from core.applications.users.services import has_org_permission
from core.applications.warehouse.access import parse_location_param
from core.applications.warehouse.models import Warehouse
from core.helper.enums import PermissionCode
from core.helper.org_time import organization_today
from core.helper.org_time import organization_tz

DEFAULT_CURRENCY = "NGN"

REPORT_TYPE_PERMISSIONS: dict[str, str] = {
    "sales": PermissionCode.VIEW_SALES_REPORTS,
    "customers": PermissionCode.VIEW_SALES_REPORTS,
    "refunds": PermissionCode.VIEW_SALES_REPORTS,
    "payments": PermissionCode.VIEW_FINANCIAL_REPORTS,
    "inventory": PermissionCode.VIEW_INVENTORY_REPORTS,
    "suppliers": PermissionCode.VIEW_INVENTORY_REPORTS,
}

OVERVIEW_PERMISSIONS = (
    PermissionCode.VIEW_FINANCIAL_REPORTS,
    PermissionCode.VIEW_SALES_REPORTS,
    PermissionCode.VIEW_INVENTORY_REPORTS,
)


class ReportPermission(BaseTenantPermission):
    """Enforces per-report permissions while reusing tenant membership resolution."""

    action_permissions: dict[str, str] = REPORT_TYPE_PERMISSIONS
    default_permission: str | None = None

    def has_permission(self, request, view) -> bool:
        if not super().has_permission(request, view):
            return False
        if getattr(request.user, "is_superuser", False):
            return True

        membership = resolve_active_membership(request, view)
        if membership is None:
            raise NotFound("Organization not found.")

        view_action = getattr(view, "action", None)
        if view_action == "list":
            return any(
                has_org_permission(request.user, membership.organization, code)
                for code in OVERVIEW_PERMISSIONS
            )
        if view_action == "export":
            report_type = (view.kwargs.get("report_type") or "").strip().lower()
            required_code = REPORT_TYPE_PERMISSIONS.get(report_type)
            if required_code is None:
                return any(
                    has_org_permission(request.user, membership.organization, code)
                    for code in OVERVIEW_PERMISSIONS
                )
            return has_org_permission(request.user, membership.organization, required_code)
        return True


@report_viewset_schema
class ReportViewSet(OrganizationScopedMixin, viewsets.ViewSet):
    """Organization-scoped and location-scoped reporting endpoints."""

    permission_classes = [IsAuthenticated, ReportPermission]

    def list(self, request, organization_id=None):
        ctx = self._context()
        granularity = _granularity(request.query_params, ("daily", "weekly", "monthly"), "monthly")
        return Response(
            {
                "meta": _meta(ctx),
                **services.overview_report(ctx, granularity=granularity),
            }
        )

    @action(detail=False, methods=["get"])
    def sales(self, request, organization_id=None):
        ctx = self._context()
        granularity = _granularity(request.query_params, ("daily", "weekly"), "daily")
        return Response({"meta": _meta(ctx), **services.sales_report(ctx, granularity=granularity)})

    @action(detail=False, methods=["get"])
    def customers(self, request, organization_id=None):
        ctx = self._context()
        return Response({"meta": _meta(ctx), **services.customer_report(ctx)})

    @action(detail=False, methods=["get"])
    def suppliers(self, request, organization_id=None):
        ctx = self._context()
        return Response({"meta": _meta(ctx), **services.supplier_report(ctx)})

    @action(detail=False, methods=["get"])
    def refunds(self, request, organization_id=None):
        ctx = self._context()
        page, page_size = _pagination(request.query_params)
        return Response(
            {"meta": _meta(ctx), **services.refund_report(ctx, page=page, page_size=page_size)}
        )

    @action(detail=False, methods=["get"])
    def payments(self, request, organization_id=None):
        ctx = self._context()
        page, page_size = _pagination(request.query_params)
        return Response(
            {"meta": _meta(ctx), **services.payment_report(ctx, page=page, page_size=page_size)}
        )

    @action(detail=False, methods=["get"])
    def inventory(self, request, organization_id=None):
        ctx = self._context()
        limit = _limit(request.query_params)
        return Response({"meta": _meta(ctx), **services.inventory_report(ctx, limit=limit)})

    @action(
        detail=False,
        methods=["get"],
        url_path=r"(?P<report_type>[a-z]+)/export",
    )
    def export(self, request, organization_id=None, report_type=None):
        normalized_type = (report_type or "").strip().lower()
        if normalized_type not in exports.EXPORTABLE_REPORT_TYPES:
            raise ValidationError(
                {
                    "report_type": [
                        f"Must be one of: {', '.join(exports.EXPORTABLE_REPORT_TYPES)}."
                    ]
                }
            )
        export_format = (request.query_params.get("export_format") or "json").strip().lower()
        if export_format not in ("json", "csv"):
            raise ValidationError({"export_format": ["Must be one of: json, csv."]})

        ctx = self._context()
        granularity = _granularity(request.query_params, ("daily", "weekly"), "daily")
        payload = exports.build_export_payload(normalized_type, ctx, granularity=granularity)

        if export_format == "csv":
            return exports.export_report_csv(
                normalized_type,
                payload,
                filename_prefix=ctx.organization.slug if hasattr(ctx.organization, "slug") else "riinox",
            )
        return Response({"meta": _meta(ctx), "report_type": normalized_type, **payload})

    # ------------------------------------------------------------------ #
    def _context(self) -> services.ReportContext:
        organization, user = self.organization, self.request.user
        scope = self.location_scope

        location = None
        location_id = parse_location_param(self.request.query_params)
        if location_id is not None:
            location = Warehouse.objects.filter(organization=organization, pk=location_id).first()
            if location is None:
                raise ValidationError({"location": ["No such location in this organization."]})
            scope.require(location)

        def can(permission_code: str) -> bool:
            return user.is_superuser or has_org_permission(user, organization, permission_code)

        return services.ReportContext(
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
            can_view_sales_reports=can(PermissionCode.VIEW_SALES_REPORTS),
            can_view_inventory_reports=can(PermissionCode.VIEW_INVENTORY_REPORTS),
            can_view_financial_reports=can(PermissionCode.VIEW_FINANCIAL_REPORTS),
        )


def _meta(ctx: services.ReportContext) -> dict:
    return {
        "period": ctx.period.as_dict(),
        "location": {"id": str(ctx.location.pk), "name": ctx.location.name} if ctx.location else None,
        "all_locations": ctx.location is None,
        "currency": ctx.currency,
        "timezone": str(ctx.tz),
        "has_profit_permission": ctx.can_view_financial_reports or ctx.can_view_profit,
    }


def _granularity(params, allowed: tuple[str, ...], default: str) -> str:
    raw = params.get("granularity")
    if raw in (None, ""):
        return default
    value = raw.strip().lower()
    if value not in allowed:
        raise ValidationError({"granularity": [f"Must be one of: {', '.join(allowed)}."]})
    return value


def _pagination(params) -> tuple[int, int]:
    page = _parse_positive_int(params.get("page"), "page", default=1, maximum=10000)
    page_size = _parse_positive_int(
        params.get("page_size"),
        "page_size",
        default=services.DEFAULT_PAGE_SIZE,
        maximum=services.MAX_PAGE_SIZE,
    )
    return page, page_size


def _limit(params) -> int:
    return _parse_positive_int(
        params.get("limit"),
        "limit",
        default=services.DEFAULT_PAGE_SIZE,
        maximum=services.MAX_PAGE_SIZE,
    )


def _parse_positive_int(raw, field_name: str, default: int, maximum: int) -> int:
    if raw in (None, ""):
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ValidationError(
            {field_name: [f"Must be a whole number from 1 to {maximum}."]}
        ) from None
    if not 1 <= value <= maximum:
        raise ValidationError({field_name: [f"Must be a whole number from 1 to {maximum}."]})
    return value
