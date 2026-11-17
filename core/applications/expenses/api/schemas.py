from __future__ import annotations

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample
from drf_spectacular.utils import OpenApiParameter
from drf_spectacular.utils import OpenApiResponse
from drf_spectacular.utils import extend_schema
from drf_spectacular.utils import extend_schema_view

from core.applications.expenses.api.serializers import ExpenseCreateSerializer
from core.applications.expenses.api.serializers import ExpenseSerializer
from core.applications.expenses.api.serializers import ExpenseUpdateSerializer
from core.applications.expenses.api.serializers import ExpenseVoidSerializer
from core.applications.warehouse.api.schemas import LOCATION_ACCESS_NOTE
from core.applications.warehouse.api.schemas import LOCATION_FILTER_PARAM
from core.helper.enums import ExpenseCategory

TAGS = ["Expenses"]
CATEGORIES = ", ".join(f"`{value}` ({label})" for value, label in ExpenseCategory.choices)

_EXAMPLE = {
    "id": "6f1c2a9e-3b7d-4c55-9a1e-2d4b8c7e9f10",
    "expense_number": "EXP-000042",
    "status": "recorded",
    "category": "fuel",
    "category_display": "Fuel / Generator",
    "title": "Diesel for generator",
    "amount": "45000.00",
    "currency": "NGN",
    "expense_date": "2026-11-17",
    "payment_method": "cash",
    "paid_to": "Total Filling Station",
    "reference": "RCP-88213",
    "notes": "",
    "location": {"id": "495e06a8-d874-489e-96c9-9fc9c10a450e", "name": "Lekki"},
    "recorded_by": {"id": 7, "name": "Emeka Obi"},
    "created_at": "2026-11-17T09:12:00Z",
    "updated_at": "2026-11-17T09:12:00Z",
    "voided_by": None,
    "voided_at": None,
    "void_reason": "",
}

_FILTERS = [
    LOCATION_FILTER_PARAM,
    OpenApiParameter("category", OpenApiTypes.STR, description=f"One of: {', '.join(ExpenseCategory.values)}."),
    OpenApiParameter("status", OpenApiTypes.STR, description="`recorded` or `void`. Default: both."),
    OpenApiParameter("date_from", OpenApiTypes.DATE, description="expense_date on or after (YYYY-MM-DD)."),
    OpenApiParameter("date_to", OpenApiTypes.DATE, description="expense_date on or before (YYYY-MM-DD)."),
    OpenApiParameter("search", OpenApiTypes.STR, description="Expense number, title, payee or reference."),
    OpenApiParameter("ordering", OpenApiTypes.STR, description="`expense_date`, `amount`, `created_at` (prefix `-` for desc)."),
]

expense_schema = extend_schema_view(
    list=extend_schema(
        tags=TAGS,
        summary="List expenses",
        description=(
            "Operating expenses (rent, fuel, salaries, ...), newest first, paginated.\n\n"
            "Voided expenses are included (with `status: void`) unless you filter `?status=recorded`.\n\n"
            "**Permission:** `VIEW_EXPENSES`.\n\n" + LOCATION_ACCESS_NOTE
        ),
        parameters=_FILTERS,
        responses={200: ExpenseSerializer(many=True)},
        examples=[OpenApiExample("Expense", value=_EXAMPLE, response_only=True)],
    ),
    retrieve=extend_schema(tags=TAGS, summary="Expense detail", responses={200: ExpenseSerializer}),
    create=extend_schema(
        tags=TAGS,
        summary="Record an expense",
        description=(
            "Records money spent at a location. In one atomic step it saves the expense, posts it to the "
            "ledger (Dr Operating Expenses / Cr Cash-Bank) and writes an audit event. It then counts in the "
            "dashboard's Expenses and Net Profit.\n\n"
            f"**`category`:** {CATEGORIES}.\n\n"
            "**`payment_method`:** same list as sales (`cash`, `pos`, `card`, `bank_transfer`, ...).\n\n"
            "**`expense_date`:** defaults to today in the organization's timezone; a future date is "
            "`400 EXPENSE_DATE_IN_FUTURE`.\n\n"
            "**`location`:** must be one of the caller's locations (`403 LOCATION_ACCESS_DENIED`).\n\n"
            "Counts as one transaction toward the plan's monthly limit.\n\n"
            "**Permission:** `RECORD_EXPENSE`."
        ),
        request=ExpenseCreateSerializer,
        examples=[
            OpenApiExample(
                "Fuel",
                request_only=True,
                value={
                    "location": "495e06a8-d874-489e-96c9-9fc9c10a450e",
                    "category": "fuel",
                    "title": "Diesel for generator",
                    "amount": "45000.00",
                    "payment_method": "cash",
                    "paid_to": "Total Filling Station",
                    "reference": "RCP-88213",
                },
            ),
            OpenApiExample("Recorded", value=_EXAMPLE, response_only=True, status_codes=["201"]),
        ],
        responses={201: ExpenseSerializer, 400: OpenApiResponse(description="Validation error.")},
    ),
    partial_update=extend_schema(
        tags=TAGS,
        summary="Edit an expense's details",
        description=(
            "Only descriptive fields can change: `category`, `title`, `expense_date`, `paid_to`, "
            "`reference`, `notes`.\n\n"
            "`amount`, `location` and `payment_method` are already in the ledger, so sending them is a `400`: "
            "void the expense and record a new one instead. A voided expense can't be edited "
            "(`409 EXPENSE_NOT_EDITABLE`). Every change is audit-logged with old and new values.\n\n"
            "**Permission:** `RECORD_EXPENSE`."
        ),
        request=ExpenseUpdateSerializer,
        responses={200: ExpenseSerializer},
    ),
    void=extend_schema(
        tags=TAGS,
        summary="Void an expense",
        description=(
            "Cancels a wrongly recorded expense. It is never deleted: it is marked `void`, the reversing "
            "ledger entries are posted, and it stops counting on the dashboard.\n\n"
            "Voiding twice is `409 EXPENSE_ALREADY_VOID`.\n\n**Permission:** `VOID_EXPENSE` (Owner / Admin)."
        ),
        request=ExpenseVoidSerializer,
        examples=[OpenApiExample("Void", request_only=True, value={"reason": "Entered twice"})],
        responses={
            200: ExpenseSerializer,
            409: OpenApiResponse(description="`EXPENSE_ALREADY_VOID`."),
        },
    ),
)
