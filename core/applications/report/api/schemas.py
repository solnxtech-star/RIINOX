from __future__ import annotations

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample
from drf_spectacular.utils import OpenApiParameter
from drf_spectacular.utils import OpenApiResponse
from drf_spectacular.utils import extend_schema
from drf_spectacular.utils import extend_schema_view

from core.applications.dashboard.period import PERIODS
from core.applications.report.api.serializers import CustomerReportResponseSerializer
from core.applications.report.api.serializers import InventoryReportResponseSerializer
from core.applications.report.api.serializers import PaymentReportResponseSerializer
from core.applications.report.api.serializers import RefundReportResponseSerializer
from core.applications.report.api.serializers import ReportOverviewResponseSerializer
from core.applications.report.api.serializers import SalesReportResponseSerializer
from core.applications.report.api.serializers import SupplierReportResponseSerializer
from core.applications.warehouse.api.schemas import LOCATION_FILTER_PARAM

TAGS = ["Reports"]

PERIOD_PARAMS = [
    OpenApiParameter(
        "period",
        OpenApiTypes.STR,
        description=(
            f"One of: {', '.join(f'`{key}`' for key in PERIODS)}. Default `this_month`. "
            "Dates are resolved in the organization's timezone."
        ),
    ),
    OpenApiParameter(
        "start_date",
        OpenApiTypes.DATE,
        description="Required when `period=custom` (YYYY-MM-DD).",
    ),
    OpenApiParameter(
        "end_date",
        OpenApiTypes.DATE,
        description="Required when `period=custom` (YYYY-MM-DD, max 366 days, not in future).",
    ),
    LOCATION_FILTER_PARAM,
]

PAGINATION_PARAMS = [
    OpenApiParameter("page", OpenApiTypes.INT, description="Page number (default 1)."),
    OpenApiParameter("page_size", OpenApiTypes.INT, description="Rows per page, 1-100 (default 5)."),
]

_META_EXAMPLE = {
    "period": {
        "key": "this_month",
        "label": "This Month",
        "start_date": "2026-10-01",
        "end_date": "2026-10-10",
        "previous_start_date": "2026-09-01",
        "previous_end_date": "2026-09-10",
    },
    "location": {"id": "16aa3955-7247-4462-a466-34432e0ba258", "name": "Port Harcourt"},
    "all_locations": False,
    "currency": "NGN",
    "timezone": "Africa/Lagos",
    "has_profit_permission": True,
}

OVERVIEW_EXAMPLE = {
    "meta": _META_EXAMPLE,
    "gross_profit_over_time": {
        "granularity": "monthly",
        "total": "1280000.00",
        "date_range_label": "1 to 10 Oct",
        "points": [
            {"label": "Sep 2026", "start": "2026-09-01", "end": "2026-09-30", "value": "3700000.00", "is_partial": False},
            {"label": "Oct 2026", "start": "2026-10-01", "end": "2026-10-10", "value": "1280000.00", "is_partial": True},
        ],
        "insight": "Gross profit was highest in Sep 2026, at ₦3,700,000.",
    },
    "sales_by_category": {
        "total": "10632360.00",
        "items": [
            {"category_id": "a1b2c3d4-0000-0000-0000-000000000001", "name": "Grains and flour", "amount": "4359267.60", "percentage": "41.0"},
        ],
        "insight": "Grains and flour made up 41.0% of this month sales.",
    },
    "payment_status_and_methods": {
        "total": "10600000.00",
        "period_label": "This Month",
        "segments": [
            {"key": "bank_transfer", "label": "Bank transfer", "amount": "4876000.00", "percentage": "46.0"},
            {"key": "not_yet_paid", "label": "Not yet paid", "amount": "1272000.00", "percentage": "12.0"},
        ],
        "insight": "Bank transfer was the most used way to pay, at 46.0%.",
    },
    "browse_reports": [
        {"key": "sales", "title": "Sales", "description": "Sales summary, best sellers and sales by customer"},
        {"key": "inventory", "title": "Inventory", "description": "Stock value, what is running low and what is expiring"},
    ],
}

report_viewset_schema = extend_schema_view(
    list=extend_schema(
        tags=TAGS,
        summary="Reports Overview",
        description=(
            "Returns the Reports landing view: gross profit over time (null without "
            "`VIEW_FINANCIAL_REPORTS` or `VIEW_DASHBOARD_PROFIT`), sales by category, "
            "payment status & methods, and available sub-reports for the caller."
        ),
        parameters=[
            *PERIOD_PARAMS,
            OpenApiParameter(
                "granularity",
                OpenApiTypes.STR,
                description="One of `daily`, `weekly`, `monthly` (default `monthly`).",
            ),
        ],
        responses={
            200: OpenApiResponse(
                response=ReportOverviewResponseSerializer,
                description="Reports overview payload.",
                examples=[OpenApiExample("Overview", value=OVERVIEW_EXAMPLE, response_only=True)],
            ),
            403: OpenApiResponse(description="Missing report permission or location access denied."),
            404: OpenApiResponse(description="Organization not found."),
        },
    ),
    sales=extend_schema(
        tags=TAGS,
        summary="Sales Report",
        description="Sales over time, top products by sales, and top customers by sales. Requires `VIEW_SALES_REPORTS`.",
        parameters=[
            *PERIOD_PARAMS,
            OpenApiParameter("granularity", OpenApiTypes.STR, description="One of `daily`, `weekly` (default `daily`)."),
        ],
        responses={200: SalesReportResponseSerializer},
    ),
    customers=extend_schema(
        tags=TAGS,
        summary="Customer Report",
        description="New customers over time, outstanding customer balances, and sales by customer type. Requires `VIEW_SALES_REPORTS`.",
        parameters=PERIOD_PARAMS,
        responses={200: CustomerReportResponseSerializer},
    ),
    suppliers=extend_schema(
        tags=TAGS,
        summary="Supplier Report",
        description="Purchases over time, spending by supplier, and outstanding supplier balances. Requires `VIEW_INVENTORY_REPORTS`.",
        parameters=PERIOD_PARAMS,
        responses={200: SupplierReportResponseSerializer},
    ),
    refunds=extend_schema(
        tags=TAGS,
        summary="Refund Report",
        description="Refunds over time and itemized refunds table. Requires `VIEW_SALES_REPORTS`.",
        parameters=[*PERIOD_PARAMS, *PAGINATION_PARAMS],
        responses={200: RefundReportResponseSerializer},
    ),
    payments=extend_schema(
        tags=TAGS,
        summary="Payment Report",
        description="Payments received over time and paginated payments table. Requires `VIEW_FINANCIAL_REPORTS`.",
        parameters=[*PERIOD_PARAMS, *PAGINATION_PARAMS],
        responses={200: PaymentReportResponseSerializer},
    ),
    inventory=extend_schema(
        tags=TAGS,
        summary="Inventory Report",
        description="Current-state snapshot of stock value by category, slow-moving stock, low stock, and expiring batches. Requires `VIEW_INVENTORY_REPORTS`.",
        parameters=[
            LOCATION_FILTER_PARAM,
            OpenApiParameter("limit", OpenApiTypes.INT, description="Max rows for low stock & expiring soon (1-100, default 5)."),
        ],
        responses={200: InventoryReportResponseSerializer},
    ),
    export=extend_schema(
        tags=TAGS,
        summary="Export Report Data",
        description=(
            "Returns complete, un-truncated report data in JSON (for frontend PDF rendering) "
            "or CSV (`?export_format=csv`) for any of: `sales`, `customers`, `suppliers`, "
            "`refunds`, `payments`, `inventory`."
        ),
        parameters=[
            *PERIOD_PARAMS,
            OpenApiParameter("granularity", OpenApiTypes.STR, description="For `sales`: `daily` or `weekly`."),
            OpenApiParameter("export_format", OpenApiTypes.STR, description="`json` (default) or `csv`."),
        ],
        responses={
            200: OpenApiResponse(description="Structured JSON export payload or CSV attachment."),
            400: OpenApiResponse(description="Unsupported report type or invalid query parameters."),
            403: OpenApiResponse(description="Missing permission for the requested report type."),
        },
    ),
)
