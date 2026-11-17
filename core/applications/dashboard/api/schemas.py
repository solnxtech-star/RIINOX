from __future__ import annotations

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample
from drf_spectacular.utils import OpenApiParameter
from drf_spectacular.utils import OpenApiResponse
from drf_spectacular.utils import extend_schema
from drf_spectacular.utils import extend_schema_view

from core.applications.dashboard.period import PERIODS
from core.applications.warehouse.api.schemas import LOCATION_FILTER_PARAM

TAGS = ["Dashboard"]

PERIOD_PARAMS = [
    OpenApiParameter(
        "period",
        OpenApiTypes.STR,
        description=(
            f"One of: {', '.join(f'`{key}`' for key in PERIODS)}. Default `this_month`. Dates are in the "
            "organization's timezone. 'This …' periods run to today and are compared with the same span of the "
            "previous period (1-17 Nov vs 1-17 Oct)."
        ),
    ),
    OpenApiParameter("start_date", OpenApiTypes.DATE, description="Required with `period=custom` (YYYY-MM-DD)."),
    OpenApiParameter("end_date", OpenApiTypes.DATE, description="Required with `period=custom`. Max 366 days, not in the future."),
    LOCATION_FILTER_PARAM,
]
LIMIT_PARAM = OpenApiParameter("limit", OpenApiTypes.INT, description="Rows per list section, 1-20. Default 5.")

COMMON = """
**Filters (same on every dashboard endpoint):** `period` (the period dropdown), `start_date`/`end_date`
for `custom`, and `location` (the branch dropdown; omit for "All branches"). A member limited to some
branches only ever sees those branches; another branch's id is `403 LOCATION_ACCESS_DENIED`.

**`meta`** echoes the resolved period (including the comparison dates), location, currency and timezone.
Use `meta.has_profit_permission` to decide whether to show the Expenses and Net Profit cards.

**Permission:** `VIEW_DASHBOARD`. Cost and profit figures also need `VIEW_DASHBOARD_PROFIT` (null otherwise);
recent activity needs `VIEW_AUDIT_LOGS` and access to every branch.

**Freshness:** figures are computed from live data and cached for about a minute.
"""

OVERVIEW_DOC = """
The four cards. Each card is `{value, previous_value, change_percent, trend}`:

- `change_percent`: % vs the comparison period, 1 dp, as a string (e.g. `"5.2"`, `"-5.2"`); **null** when the
  previous value is 0 (show "—" or "New"). `trend`: `up` / `down` / `flat`. For Refunds, **up is bad**:
  colour it red.

Definitions (all exclude tax):
- **Sales** (`cards.sales`): invoices issued in the period (every sale issues one; standalone invoices too), net
  of discounts, minus invoices voided / sales cancelled in the period. `count` = live sales in the period.
- **Refunds**: money returned to customers on sale returns in the period.
- **Expenses** (`null` without `VIEW_DASHBOARD_PROFIT`): recorded (not voided) operating expenses dated in the period.
- **Net profit** (`null` without `VIEW_DASHBOARD_PROFIT`): Sales − cost of goods sold − Expenses − Refunds.
  `cost_of_goods_sold` is included so the UI can explain the figure.
"""

_META = {
    "period": {
        "key": "this_month", "label": "This Month", "start_date": "2026-11-01", "end_date": "2026-11-17",
        "previous_start_date": "2026-10-01", "previous_end_date": "2026-10-17",
    },
    "location": {"id": "16aa3955-7247-4462-a466-34432e0ba258", "name": "Main Branch"},
    "all_locations": False,
    "currency": "NGN",
    "timezone": "Africa/Lagos",
    "has_profit_permission": True,
}
_CARD = {"value": "650400000.00", "previous_value": "618250000.00", "change_percent": "5.2", "trend": "up"}
OVERVIEW_EXAMPLE = {
    "cards": {
        "sales": {**_CARD, "count": 1284},
        "expenses": {"value": "210200000.00", "previous_value": "199800000.00", "change_percent": "5.2", "trend": "up"},
        "net_profit": {"value": "440200000.00", "previous_value": "414500000.00", "change_percent": "6.2",
                       "trend": "up", "cost_of_goods_sold": "0.00"},
        "refunds": {"value": "45000000.00", "previous_value": "47470000.00", "change_percent": "-5.2", "trend": "down"},
    }
}
CHART_EXAMPLE = {
    "granularity": "week",
    "total": "65400000.00", "previous_total": "58185000.00", "change_percent": "12.4", "trend": "up",
    "max_value": "500000.00",
    "points": [
        {"label": "W1", "start": "2026-11-01", "end": "2026-11-07", "value": "100000.00"},
        {"label": "W2", "start": "2026-11-08", "end": "2026-11-14", "value": "500000.00"},
        {"label": "W3", "start": "2026-11-15", "end": "2026-11-17", "value": "400000.00"},
    ],
}
ALERTS_EXAMPLE = {
    "count": 3,
    "items": [
        {"type": "low_stock", "severity": "urgent", "title": "Low Stock Alert",
         "message": "12 products are below their reorder level", "count": 12,
         "link": {"resource": "products", "query": {"low_stock": "true"}}},
        {"type": "overdue_payment", "severity": "urgent", "title": "Overdue Payment",
         "message": "₦125,500 from ABC Stores is overdue", "count": 1,
         "link": {"resource": "invoices", "query": {"overdue": "true"}}, "amount": "125500.00",
         "top": {"invoice_id": "eb23a57e-58ac-4906-bed8-0801f5a4f302", "invoice_number": "INV-000123",
                 "customer_name": "ABC Stores", "balance": "125500.00", "due_date": "2026-11-01"}},
        {"type": "expiry_warning", "severity": "warning", "title": "Expiry Warning",
         "message": "7 products are expiring within 30 days", "count": 7, "link": None},
    ],
}
TOP_EXAMPLE = {
    "max_units_sold": 48,
    "items": [
        {"rank": 1, "product": {"id": "3c8b2899-36c1-48c2-a2bb-16c5831273ae", "name": "Rice 50g", "sku": "RICE-50"},
         "units_sold": 48, "revenue": "96000.00", "share_of_top_percent": "100.0"},
        {"rank": 2, "product": {"id": "9a0e…", "name": "Pet Coke", "sku": "PET-COKE"},
         "units_sold": 44, "revenue": "11000.00", "share_of_top_percent": "91.7"},
    ],
}
ACTIVITY_EXAMPLE = {
    "available": True,
    "items": [
        {"id": "…", "action": "inventory.opening_stock_set", "message": "Ada added opening stock for 50 units of Premium Rice",
         "actor": {"id": 12, "name": "Ada"}, "resource": {"type": "products.product", "id": "…", "label": "Premium Rice"},
         "occurred_at": "2026-11-17T09:32:00+00:00"},
        {"id": "…", "action": "expense.recorded", "message": "Emeka recorded expense Fuel - Gen (₦45,000)",
         "actor": {"id": 9, "name": "Emeka"}, "resource": {"type": "expenses.expense", "id": "…", "label": "EXP-000042"},
         "occurred_at": "2026-11-17T09:22:00+00:00"},
    ],
}
TRANSACTIONS_EXAMPLE = {
    "total": 100,
    "items": [
        {"id": "…", "type": "sale", "type_display": "Sale", "reference": "SALE-2026-0089", "amount": "45000.00",
         "status": "pending", "status_display": "Pending", "branch": {"id": "…", "name": "Port Harcourt"},
         "occurred_at": "2026-11-17T10:42:00+00:00"},
        {"id": "…", "type": "expense", "type_display": "Operational Expenses", "reference": "EXP-000042",
         "amount": "10000000.00", "status": "successful", "status_display": "Successful",
         "branch": {"id": "…", "name": "Port Harcourt"}, "occurred_at": "2026-11-16T22:00:00+00:00"},
    ],
}


def _ok(example: dict, name: str) -> OpenApiResponse:
    return OpenApiResponse(
        response=OpenApiTypes.OBJECT,
        examples=[OpenApiExample(name, value={"meta": _META, **example}, response_only=True)],
    )


ERRORS = {
    400: OpenApiResponse(description="Bad `period`, dates, `limit` or `location` (`VALIDATION_ERROR`)."),
    403: OpenApiResponse(description="Missing `VIEW_DASHBOARD`, or `LOCATION_ACCESS_DENIED`."),
}

dashboard_schema = extend_schema_view(
    list=extend_schema(
        tags=TAGS,
        summary="Whole dashboard",
        description=(
            "Every section in one call, for the first paint: `overview`, `sales_chart`, `alerts`, `top_products`, "
            "`recent_activity`, `recent_transactions`. Each section has its own endpoint too (same shapes) so a "
            "card can refresh alone.\n" + COMMON
        ),
        parameters=[*PERIOD_PARAMS, LIMIT_PARAM],
        responses={
            200: OpenApiResponse(
                response=OpenApiTypes.OBJECT,
                examples=[
                    OpenApiExample(
                        "Dashboard",
                        response_only=True,
                        value={
                            "meta": _META, "overview": OVERVIEW_EXAMPLE, "sales_chart": CHART_EXAMPLE,
                            "alerts": ALERTS_EXAMPLE, "top_products": TOP_EXAMPLE,
                            "recent_activity": ACTIVITY_EXAMPLE, "recent_transactions": TRANSACTIONS_EXAMPLE,
                        },
                    )
                ],
            ),
            **ERRORS,
        },
    ),
    overview=extend_schema(
        tags=TAGS, summary="Overview cards (Sales, Expenses, Net Profit, Refunds)",
        description=OVERVIEW_DOC + COMMON, parameters=PERIOD_PARAMS,
        responses={200: _ok(OVERVIEW_EXAMPLE, "Overview"), **ERRORS},
    ),
    sales_chart=extend_schema(
        tags=TAGS, summary="Sales overview chart",
        description=(
            "Net sales (same definition as the Sales card) per bucket, with every bucket present (zeros "
            "included) so the line never has gaps.\n\n"
            "`granularity` follows the period length: 1 day → `hour` (24 points, `00:00`…), up to 14 days → "
            "`day`, up to 92 days → `week` (`W1`, `W2`… counted from the period start), longer → `month`. "
            "`max_value` helps scale the Y axis. `total`/`change_percent` feed the big number and badge.\n"
            + COMMON
        ),
        parameters=PERIOD_PARAMS, responses={200: _ok(CHART_EXAMPLE, "Chart"), **ERRORS},
    ),
    alerts=extend_schema(
        tags=TAGS, summary="Smart alerts",
        description=(
            "Things that need attention **now** (they ignore `period`, but respect `location`). Only alerts with "
            "something to report are returned, most urgent first.\n\n"
            "`type`: `out_of_stock`, `low_stock` (stock > 0 and ≤ the product's minimum), `overdue_payment` "
            "(confirmed invoices past due with a balance; `top` = the largest), `expired_stock`, "
            "`expiry_warning` (batches expiring within 30 days, with stock), `pending_approvals` (stock "
            "adjustments; only for users who can approve). `severity`: `urgent` / `warning` / `info`. `link` "
            "tells the UI which list to open with which filters (null when there is no matching list filter).\n"
            + COMMON
        ),
        parameters=PERIOD_PARAMS, responses={200: _ok(ALERTS_EXAMPLE, "Alerts"), **ERRORS},
    ),
    top_products=extend_schema(
        tags=TAGS, summary="Top performing products",
        description=(
            "Best sellers in the period by units sold (base units, so cartons and pieces add up), then revenue "
            "(net of discounts, excluding tax). Cancelled and voided sales are excluded.\n\n"
            "For the progress bars: `units_sold` / `max_units_sold` (the best seller fills the bar), or use "
            "`share_of_top_percent` directly.\n" + COMMON
        ),
        parameters=[*PERIOD_PARAMS, LIMIT_PARAM], responses={200: _ok(TOP_EXAMPLE, "Top products"), **ERRORS},
    ),
    activity=extend_schema(
        tags=TAGS, summary="Recent activity",
        description=(
            "Latest actions in the business from the audit log, newest first, each with a ready-to-show "
            "`message` (\"Emeka recorded expense Fuel - Gen (₦45,000)\") and `occurred_at` for \"10 mins ago\".\n\n"
            "Ignores `period`. Needs `VIEW_AUDIT_LOGS` **and** access to every branch (audit events are "
            "organization-wide); otherwise `available: false` and no items: hide the card.\n" + COMMON
        ),
        parameters=[LIMIT_PARAM], responses={200: _ok(ACTIVITY_EXAMPLE, "Activity"), **ERRORS},
    ),
    recent_transactions=extend_schema(
        tags=TAGS, summary="Recent transactions",
        description=(
            "Newest money events in the period across sales, refunds (sale returns), purchases and operating "
            "expenses (expenses only with `VIEW_DASHBOARD_PROFIT`). `total` is how many there are in the period "
            "(\"Showing 4 of 100 entries\").\n\n"
            "`type`: `sale`, `refund`, `purchase`, `expense` (`type_display` \"Operational Expenses\"). "
            "`status`: `successful`, `pending`, `failed`, `cancelled`, `refunded`. A sale is `pending` until fully "
            "paid; a purchase until its goods are received. `amount` for a purchase is the ordered value.\n"
            + COMMON
        ),
        parameters=[*PERIOD_PARAMS, LIMIT_PARAM],
        responses={200: _ok(TRANSACTIONS_EXAMPLE, "Recent transactions"), **ERRORS},
    ),
)
