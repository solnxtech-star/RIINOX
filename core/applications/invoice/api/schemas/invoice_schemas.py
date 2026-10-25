
from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from typing import Final

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample
from drf_spectacular.utils import OpenApiParameter
from drf_spectacular.utils import OpenApiResponse
from drf_spectacular.utils import extend_schema
from drf_spectacular.utils import extend_schema_view
from rest_framework import status

from core.applications.invoice.api.constants import EXPORT_COLUMNS
from core.applications.invoice.api.constants import MAX_EXPORT_ROWS
from core.applications.invoice.api.serializers.invoice_serializers import (
    _SERVER_OWNED_FIELDS,
)
from core.applications.invoice.api.serializers.invoice_serializers import ARCHIVE_SCOPES
from core.applications.invoice.api.serializers.invoice_serializers import (
    MAX_LINE_TEXT_LENGTH,
)
from core.applications.invoice.api.serializers.invoice_serializers import (
    MAX_NOTES_LENGTH,
)
from core.applications.invoice.api.serializers.invoice_serializers import (
    MAX_REASON_LENGTH,
)
from core.applications.invoice.api.serializers.invoice_serializers import (
    MAX_SEARCH_LENGTH,
)
from core.applications.invoice.api.serializers.invoice_serializers import (
    MAX_UNIT_NAME_LENGTH,
)
from core.applications.invoice.api.serializers.invoice_serializers import (
    AuditTrailPageSerializer,
)
from core.applications.invoice.api.serializers.invoice_serializers import (
    AuditTrailQuerySerializer,
)
from core.applications.invoice.api.serializers.invoice_serializers import (
    InvoiceArchiveSerializer,
)
from core.applications.invoice.api.serializers.invoice_serializers import (
    InvoiceConfirmSerializer,
)
from core.applications.invoice.api.serializers.invoice_serializers import (
    InvoiceCreateSerializer,
)
from core.applications.invoice.api.serializers.invoice_serializers import (
    InvoiceDetailSerializer,
)
from core.applications.invoice.api.serializers.invoice_serializers import (
    InvoiceListFilterSerializer,
)
from core.applications.invoice.api.serializers.invoice_serializers import (
    InvoiceListSerializer,
)
from core.applications.invoice.api.serializers.invoice_serializers import (
    InvoiceStateSerializer,
)
from core.applications.invoice.api.serializers.invoice_serializers import (
    InvoiceSummarySerializer,
)
from core.applications.invoice.api.serializers.invoice_serializers import (
    InvoiceUnarchiveSerializer,
)
from core.applications.invoice.api.serializers.invoice_serializers import (
    InvoiceUpdateSerializer,
)
from core.applications.invoice.api.serializers.invoice_serializers import (
    InvoiceVoidAndCorrectSerializer,
)
from core.applications.invoice.api.serializers.invoice_serializers import (
    InvoiceVoidSerializer,
)
from core.applications.invoice.queryset import ORDERING_FIELDS
from core.applications.invoice.services.invoices import MAX_IDEMPOTENCY_KEY_LENGTH
from core.applications.invoice.services.invoices import MAX_INVOICE_LINES
from core.applications.invoice.services.invoices import MAX_LINE_QUANTITY
from core.applications.invoice.services.invoices import MIN_VOID_REASON_LENGTH
from core.applications.invoice.services.ports import PERM_AUDIT_VIEW
from core.applications.invoice.services.ports import PERM_INVOICE_ARCHIVE
from core.applications.invoice.services.ports import PERM_INVOICE_CONFIRM
from core.applications.invoice.services.ports import PERM_INVOICE_CREATE
from core.applications.invoice.services.ports import PERM_INVOICE_VIEW
from core.applications.invoice.services.ports import PERM_INVOICE_VOID
from core.helper.custom_exceptions import CustomError
from core.helper.custom_exceptions import get_all_schema
from core.helper.enums import InvoiceStatusChoices

__all__ = ["INVOICE_API_GUIDE", "INVOICE_TAG", "invoice_viewset_schema"]

TAG_NAME: Final = "Invoices"
_TAGS: Final = [TAG_NAME]


# =========================================================================== #
# 1. The guide shown once, on the "Invoices" tag
# =========================================================================== #

_STATUSES = ", ".join(f"`{value}` ({label})" for value, label in InvoiceStatusChoices.choices)
_ORDERING = ", ".join(f"`{name}`" for name in sorted(ORDERING_FIELDS))
_SERVER_OWNED = ", ".join(f"`{name}`" for name in sorted(_SERVER_OWNED_FIELDS))
_SCOPES = ", ".join(f"`{scope}`" for scope in ARCHIVE_SCOPES)
_EXPORT_COLUMNS = ", ".join(EXPORT_COLUMNS)

_PERMISSION_ROWS = (
    ("List, summary, CSV export, detail", PERM_INVOICE_VIEW),
    ("Create a Draft, replace a Draft's lines", PERM_INVOICE_CREATE),
    ("Confirm", PERM_INVOICE_CONFIRM),
    ("Void, void and correct", PERM_INVOICE_VOID),
    ("Archive, unarchive", PERM_INVOICE_ARCHIVE),
    ("Audit trail", PERM_AUDIT_VIEW),
)
_PERMISSION_TABLE = "\n".join(f"| {action} | `{code}` |" for action, code in _PERMISSION_ROWS)

INVOICE_API_GUIDE: Final = f"""
Customer invoices of one organization. Every path starts with
`/api/organizations/<organization_id>/invoices/`.

## Lifecycle

Statuses: {_STATUSES}.

1. **Create** makes a standalone **Draft**. A Draft is editable. It moves no
   stock and does not touch the customer's balance.
2. **Confirm** turns a Draft into a real invoice. In one transaction the
   server locks the customer, checks the credit limit, deducts stock and
   freezes the totals. After that the lines, prices and totals never change.
3. **Void** cancels an invoice. The record stays forever. If the invoice was
   issued for a sale, the sale is cancelled and its stock is reversed.
4. **Void and correct** voids an invoice and opens a corrected Draft in one
   step. The new Draft points back through `replaces`, and the original lists
   it under `corrections`.
5. **Archive** hides a settled (paid or voided) invoice from the default list.
   It never changes money or stock. **Unarchive** brings it back.

There is no delete. `DELETE` and `PATCH` return `405`. Completed invoices are
historical records (PRD §44) and are changed only by voiding.

`is_overdue` is calculated by the server: the invoice still carries a balance
and its due date has passed on the organization's own calendar, partially paid
invoices included. Do not recompute it in the browser.

## Conventions

* **Organization.** It is the `organization_id` in the URL, checked against the
  caller's active membership on every request, and never read from the body.
  An id that belongs to another organization behaves exactly like an id that
  does not exist (`404`, or a "not found" validation error).
* **Ids** are UUIDs. They are accepted with or without dashes and in any case,
  and are always returned in the dashed lowercase form.
* **Money** is returned as a string with two decimals (`"1900.00"`). Send
  amounts as strings too. Amounts are never negative, and never more than 12
  digits on input.
* **Dates** are ISO 8601. `due_date` is a plain date (`2026-11-30`). Timestamps
  such as `issued_at` and `created_at` include a time zone offset.
* **Server-owned values.** The client never sends prices or totals. The server
  prices every line from the product, the variant and the unit. Sending any of
  these fields is a `400`: {_SERVER_OWNED}.
* **Unknown fields** in a request body are rejected with `Unknown field.`
  instead of being ignored, so a typo can never silently do nothing.
* **Never compute on the client.** Show the totals, balance and status exactly
  as returned. The same goes for tax and stock.

## Permissions

| Action | Permission code |
|---|---|
{_PERMISSION_TABLE}

Use `available_actions` on each invoice to decide which buttons to show. It is
already filtered by the caller's permissions and by the invoice's status. It
is a UI hint only: the API enforces the same rules and answers `403` or `409`
to a request the UI should not have offered.

## Errors

| Status | Meaning | Body |
|---|---|---|
| `400` | Invalid body or query, or a validation rule | Field errors such as `{{"notes": ["..."]}}`, or `{{"code": "INVOICE_VALIDATION_ERROR", "detail": "...", "context": {{...}}}}` |
| `401` | Not signed in | `{{"code", "detail"}}` |
| `403` | Not an active member of the organization, or the permission is missing | `{{"code", "detail"}}` |
| `404` | Invoice not found in this organization | `{{"code", "detail"}}` |
| `405` | Method not supported (`PATCH`, `DELETE`) | `{{"code", "detail"}}` |
| `409` | A business rule blocked the action | `{{"code", "detail", "context"}}` |
| `503` | The feature is not available yet | `{{"code", "detail"}}` |
| `500` | Unexpected server error | `{{"code": "INTERNAL_SERVER_ERROR", "detail": "..."}}` |

Branch on `code` for `409` responses, never on the text of `detail`. Field
errors are keyed by the request field name. Errors on a line are nested under
`lines`, with one entry per line in the order sent.
"""

INVOICE_TAG: Final = {"name": TAG_NAME, "description": INVOICE_API_GUIDE.strip()}


# =========================================================================== #
# 2. Shared building blocks
# =========================================================================== #

_CONFLICT_MEANINGS: Final = {
    "INVALID_INVOICE_TRANSITION": (
        "The invoice's current status does not allow this action. Reload the invoice and read `available_actions`."
    ),
    "INVOICE_HAS_PAYMENTS": (
        "A payment is recorded against the invoice, so it cannot be voided. Reverse the payment through the payments flow first."
    ),
    "INSUFFICIENT_STOCK": (
        "A product does not have enough stock in the invoice's warehouse. `context` names the product, the quantity "
        "requested and the quantity available, in base units. Nothing was changed."
    ),
    "CREDIT_LIMIT_EXCEEDED": (
        "Confirming would put the customer over their credit limit. Retry with a valid `credit_approval_id`, or lower the amount."
    ),
    "INVOICE_ALREADY_ISSUED_FOR_SALE": "An invoice has already been issued for the sale behind this one.",
    "APPROVAL_REQUIRED": (
        "The organization's rules require an approval for this action and the supplied approval id is missing, invalid, "
        "expired or already used. Request an approval and retry with its id."
    ),
}

_IDEMPOTENCY_HEADER: Final = OpenApiParameter(
    "Idempotency-Key",
    OpenApiTypes.STR,
    OpenApiParameter.HEADER,
    required=False,
    description=(
        "A unique value per user action, for example a UUID generated when the form opens. If the request is repeated "
        "with the same key (a double click, a retry after a timeout), the server returns the first result and does "
        f"not create a second invoice. At most {MAX_IDEMPOTENCY_KEY_LENGTH} characters."
    ),
)


def _doc(body: str, permission: str | None = None) -> str:
    """The operation description, with the permission it needs appended."""
    text = body.strip()
    if permission:
        text += f"\n\n**Permission required:** `{permission}`"
    return text


def _conflicts(*codes: str) -> OpenApiResponse:
    lines = "\n".join(f"* `{code}`: {_CONFLICT_MEANINGS[code]}" for code in codes)
    return OpenApiResponse(
        description=(
            "A business rule blocked the action. The body is `code`, `detail` and `context`. "
            "Possible codes for this endpoint:\n\n" + lines
        ),
    )


def _responses(
    status_code: int,
    serializer: Any,
    description: str,
    *,
    conflicts: Sequence[str] = (),
    extra: dict[int, Any] | None = None,
) -> dict[int, Any]:
    """The project's generic errors, this endpoint's success body, and its documented 409 and 503 cases."""
    documented: dict[int, Any] = {
        **CustomError.DEFAULT_ERROR_SCHEMA(),
        status_code: OpenApiResponse(response=serializer, description=description),
    }
    if conflicts:
        documented[status.HTTP_409_CONFLICT] = _conflicts(*conflicts)
    if extra:
        documented.update(extra)
    return documented


# =========================================================================== #
# 3. Examples
# =========================================================================== #

_INVOICE_ID = "eb23a57e-58ac-4906-bed8-0801f5a4f302"
_PRODUCT_ID = "15a9423c-e483-416d-a017-778c3daae635"
_CUSTOMER_ID = "ad6bdb10-07ce-414c-b2d4-3e4e4612ab9b"
_WAREHOUSE_ID = "3c9fa04c-39ff-403f-bfbe-284ef9391419"

_LINE_EXAMPLE = {
    "id": "5d1b7a52-9c1f-4a6e-8f3a-0c2b1e7d9a10",
    "product_id": _PRODUCT_ID,
    "product_name": "Cement 50kg",
    "variant_id": None,
    "description": "Cement 50kg",
    "quantity": 2,
    "unit_name": "carton",
    "unit_multiplier": "12.000",
    "unit_price": "1200.00",
    "discount_amount": "0.00",
    "tax_amount": "0.00",
    "tax_rate": "0.00",
    "line_total": "2400.00",
}


def _invoice_example(
    *,
    status_value: str,
    status_label: str,
    actions: list[str],
    confirmed: bool = False,
    voided: bool = False,
    replaces: dict[str, Any] | None = None,
    corrections: list[dict[str, Any]] | None = None,
    number: str = "INV-000123",
) -> dict[str, Any]:
    """An illustrative invoice detail. Real status and action values come from the server."""
    person = {"id": "0b9a6c2e-1d4f-4b7a-9e35-6a1f2c8d7e40", "name": "Ngozi Eze", "email": "ngozi@example.com"}
    return {
        "id": _INVOICE_ID,
        "invoice_number": number,
        "status": status_value,
        "status_display": status_label,
        "archived_at": None,
        "is_archived": False,
        "available_actions": actions,
        "is_overdue": False,
        "customer_id": _CUSTOMER_ID,
        "customer_name": "Adaeze Stores",
        "customer_company_name": "Adaeze Stores Ltd",
        "issued_at": "2026-10-25T09:30:00+01:00" if confirmed else None,
        "due_date": "2026-11-30",
        "currency": "NGN",
        "total": "1900.00",
        "amount_paid": "0.00",
        "balance_due": "1900.00",
        "order_code": None,
        "is_sale_origin": False,
        "warehouse": {"id": _WAREHOUSE_ID, "name": "Main Warehouse"},
        "sales_rep": person,
        "created_at": "2026-10-25T09:15:00+01:00",
        "customer_tax_id": None,
        "customer_email": "accounts@adaeze.example",
        "customer_phone": "+2348012345678",
        "billing_address": "12 Aba Road, Port Harcourt",
        "shipping_address": "12 Aba Road, Port Harcourt",
        "subtotal": "2400.00",
        "discount_amount": "500.00",
        "tax_amount": "0.00",
        "notes": "Postman test",
        "extra_data": {},
        "sale_id": None,
        "items": [_LINE_EXAMPLE],
        "confirmed_by": person if confirmed else None,
        "voided_by": person if voided else None,
        "voided_at": "2026-10-25T11:00:00+01:00" if voided else None,
        "void_reason": "Wrong quantity entered on the original invoice" if voided else None,
        "replaces": replaces,
        "corrections": corrections or [],
        "updated_at": "2026-10-25T09:30:00+01:00",
    }


_DRAFT = _invoice_example(status_value="draft", status_label="Draft", actions=["confirm"])
_CONFIRMED = _invoice_example(
    status_value="confirmed",
    status_label="Confirmed",
    actions=["void"],
    confirmed=True,
)
_VOIDED = _invoice_example(
    status_value="void",
    status_label="Void",
    actions=["archive"],
    confirmed=True,
    voided=True,
)
_CORRECTED_DRAFT = _invoice_example(
    status_value="draft",
    status_label="Draft",
    actions=["confirm"],
    number="INV-000124",
    replaces={"id": _INVOICE_ID, "invoice_number": "INV-000123", "status": "void"},
)

_ERROR_PRODUCT_NOT_FOUND = OpenApiExample(
    "Product not found",
    value={
        "code": "INVOICE_VALIDATION_ERROR",
        "detail": "Some products were not found.",
        "context": {"product_ids": ["15a9423c-e483-416d-a017-778c3daae635"]},
    },
    response_only=True,
    status_codes=["400"],
)
_ERROR_UNIT_NOT_AVAILABLE = OpenApiExample(
    "Unit not available for the product",
    value={
        "code": "INVOICE_VALIDATION_ERROR",
        "detail": "The unit 'pallet' is not available for this product.",
        "context": {"unit_name": "pallet", "product_id": "15a9423c-e483-416d-a017-778c3daae635"},
    },
    response_only=True,
    status_codes=["400"],
)
_ERROR_DISCOUNT_TOO_BIG = OpenApiExample(
    "Discount larger than the subtotal",
    value={
        "code": "INVOICE_VALIDATION_ERROR",
        "detail": "The discount cannot exceed the subtotal.",
        "context": {"subtotal": "2400.00", "discount": "99999.00"},
    },
    response_only=True,
    status_codes=["400"],
)
_ERROR_SERVER_OWNED = OpenApiExample(
    "A server-owned field was sent",
    value={"lines": [{"unit_price": ["This value is set by the server and cannot be sent."]}]},
    response_only=True,
    status_codes=["400"],
)
_ERROR_NOT_FOUND = OpenApiExample(
    "Invoice not found",
    value={"code": "NOT_FOUND", "detail": "Invoice not found."},
    response_only=True,
    status_codes=["404"],
)
_ERROR_STOCK = OpenApiExample(
    "Not enough stock",
    value={
        "code": "INSUFFICIENT_STOCK",
        "detail": "Out of stock: Cement 50kg. Requested 24, available 10.",
        "context": {"product_id": _PRODUCT_ID, "requested": "24", "available": "10"},
    },
    response_only=True,
    status_codes=["409"],
)
_ERROR_TRANSITION = OpenApiExample(
    "Action not allowed in the current status",
    value={
        "code": "INVALID_INVOICE_TRANSITION",
        "detail": "This invoice cannot be changed from its current status.",
        "context": {"invoice_id": _INVOICE_ID},
    },
    response_only=True,
    status_codes=["409"],
)


# =========================================================================== #
# 4. The schema
# =========================================================================== #

invoice_viewset_schema = extend_schema_view(
    list=extend_schema(
        tags=_TAGS,
        summary="List invoices",
        description=_doc(
            f"""
The invoice table: one page of invoices of the organization, with the filters
of the filter drawer and the search box.

**Filters.** All filters combine with AND. Leave a filter out to not apply it.

* `status`: one or more statuses, comma-separated (`?status=a,b`) or repeated
  (`?status=a&status=b`). Both forms behave the same.
* `customer`, `sales_rep`, `warehouse`: an id. An id that does not exist, or
  belongs to another organization, simply matches nothing.
* `search`: free text, at most {MAX_SEARCH_LENGTH} characters.
* `issued_from`, `issued_to`, `due_from`, `due_to`: inclusive dates. A `_to`
  earlier than its `_from` is a `400`.
* `overdue=true`: only invoices that are overdue.
* `archived`: {_SCOPES}. Defaults to `active`, which hides archived invoices.
* `ordering`: one of {_ORDERING}, with a leading `-` for descending. Leave it
  out for the server's default order.

**Pagination.** Page-number pagination with `page` and `page_size`. The
response carries `count`, `total_pages`, `current_page`, `page_size`, `next`,
`previous` and `results`. Page depth is capped, so narrow the filters instead
of paging very deep.

The summary cards above the table take these same filters (see
`GET .../summary/`), so the cards always match the rows.
""",
            PERM_INVOICE_VIEW,
        ),
        parameters=[InvoiceListFilterSerializer],
        responses=get_all_schema("invoices", InvoiceListSerializer),
    ),
    summary=extend_schema(
        tags=_TAGS,
        summary="Summary cards for the invoice list",
        description=_doc(
            """
The four cards above the invoice table. It takes the same filters as the list,
so the cards always match the rows.

| Card | Fields |
|---|---|
| Invoiced | `total` (with `subtotal`, `discount`, `tax`) |
| Paid | `paid` |
| Outstanding | `balance_due` |
| Overdue | `overdue_amount` and `overdue_count` |

**What counts.** The money fields cover only invoices that count as real
sales: confirmed and beyond. Drafts and voided invoices never inflate a
total. They are reported apart: `draft_count` (drafts and pending) and
`voided_count`. `overdue_amount` is the remaining balance of overdue invoices,
so a partially paid invoice counts only for what is still owed.

**Archived invoices** still count in the totals. Unless you filter with
`archived=archived`, the summary includes them, so closing a year's invoices
does not change your revenue figures.

With no matching invoices every money field is `"0.00"` and every count is `0`.
""",
            PERM_INVOICE_VIEW,
        ),
        parameters=[InvoiceListFilterSerializer],
        responses=_responses(status.HTTP_200_OK, InvoiceSummarySerializer, "The totals for the filtered invoices."),
        examples=[
            OpenApiExample(
                "Summary",
                value={
                    "count": 18,
                    "subtotal": "480000.00",
                    "discount": "12000.00",
                    "tax": "0.00",
                    "total": "468000.00",
                    "paid": "300000.00",
                    "balance_due": "168000.00",
                    "overdue_count": 3,
                    "overdue_amount": "54000.00",
                    "draft_count": 4,
                    "voided_count": 2,
                },
                response_only=True,
                status_codes=["200"],
            ),
        ],
    ),
    export=extend_schema(
        tags=_TAGS,
        summary="Export the filtered invoices as CSV",
        description=_doc(
            f"""
Downloads the invoices that match the filters as a CSV file. It takes the same
filters as the list, and ignores `page` and `page_size`: you always get every
matching invoice.

* **Columns:** {_EXPORT_COLUMNS}.
* **File name:** `invoices-YYYYMMDD.csv`, dated on the organization's calendar,
  sent in `Content-Disposition` as an attachment.
* **Encoding:** UTF-8. Dates are ISO 8601.
* **Limit:** more than {MAX_EXPORT_ROWS:,} matching invoices is a `400`. Narrow
  the filters, for example to one month.
* **Spreadsheet safety.** A cell that starts with `=`, `+`, `-`, `@`, a tab or
  a carriage return is prefixed with `'`, so a customer name can never run as
  a spreadsheet formula.
* The file is streamed, so large exports start downloading immediately.

Request it with a normal download (a link or a blob download), not as a JSON
call.
""",
            PERM_INVOICE_VIEW,
        ),
        parameters=[InvoiceListFilterSerializer],
        responses={
            **CustomError.DEFAULT_ERROR_SCHEMA(),
            status.HTTP_200_OK: OpenApiResponse(response=OpenApiTypes.BINARY, description="A `text/csv; charset=utf-8` file."),
        },
    ),
    retrieve=extend_schema(
        tags=_TAGS,
        summary="Invoice detail",
        description=_doc(
            """
Everything the detail page and the printable document need, in one call.

* **Lines** are in `items`. Each line is frozen: `unit_price`, `unit_multiplier`,
  `tax_rate` and `line_total` are what the customer was charged. A later change
  to the product never rewrites an issued invoice.
* **Quantities** are in the line's own unit (`unit_name`). One carton line with
  a `unit_multiplier` of `12.000` is twelve base units.
* **Contact and address fields** are copied onto the invoice when it is
  created, so they show what was true at the time.
* `replaces` is set on a corrected Draft and links to the invoice it replaced.
  `corrections` lists the invoices that were opened to correct this one.
* `voided_by`, `voided_at` and `void_reason` are filled in only for a voided
  invoice. `confirmed_by` is filled in once it is confirmed.
* `sale_id` is set when the invoice was issued for a sale (`is_sale_origin`).
* `available_actions` lists what the caller can do next. See the guide.

An invoice from another organization, or a malformed id, is a `404`.
""",
            PERM_INVOICE_VIEW,
        ),
        responses=_responses(status.HTTP_200_OK, InvoiceDetailSerializer, "The invoice."),
        examples=[
            OpenApiExample("Draft", value=_DRAFT, response_only=True, status_codes=["200"]),
            _ERROR_NOT_FOUND,
        ],
    ),
    audit=extend_schema(
        tags=_TAGS,
        summary="Audit trail of an invoice",
        description=_doc(
            """
The Audit tab of the invoice detail page: who did what, when, from where, and
what changed. Events are immutable and listed **newest first**.

Audit access is a separate permission from invoice access. A user who can open
invoices but lacks it gets a `403`, so hide the tab for them.

**Pagination uses a cursor, not page numbers.** Send `limit` (1 to 100,
default 50). The response has `results` and `next_cursor`. To load more, call
again with `cursor=<next_cursor>`. `next_cursor` is `null` on the last page.
The cursor is opaque: pass it back exactly as received and never build one. A
malformed cursor is a `400`.

Each event carries `action`, `actor`, `reason`, `approval_id`,
`previous_values`, `new_values`, `metadata`, `ip_address`, `user_agent` and
`created_at`. `previous_values` and `new_values` are free-form JSON: render
them as a before/after diff and do not rely on a fixed set of keys.
""",
            PERM_AUDIT_VIEW,
        ),
        parameters=[AuditTrailQuerySerializer],
        responses=_responses(status.HTTP_200_OK, AuditTrailPageSerializer, "One page of audit events."),
        examples=[
            OpenApiExample(
                "A page of events",
                value={
                    "results": [
                        {
                            "id": "9c0f4b61-7a52-4d0e-8e1b-3f6a2d5b7c18",
                            "action": "invoice.confirmed",
                            "actor": {"id": "0b9a6c2e-1d4f-4b7a-9e35-6a1f2c8d7e40", "name": "Ngozi Eze", "email": "ngozi@example.com"},
                            "reason": None,
                            "approval_id": None,
                            "previous_values": {"status": "draft"},
                            "new_values": {"status": "confirmed"},
                            "metadata": {},
                            "ip_address": "102.89.34.10",
                            "user_agent": "Mozilla/5.0",
                            "created_at": "2026-10-25T09:30:00+01:00",
                        },
                    ],
                    "next_cursor": "MjAyNi0xMC0yNVQwODozMDowMCswMDowMHwxMjM",
                },
                response_only=True,
                status_codes=["200"],
            ),
        ],
    ),
    create=extend_schema(
        tags=_TAGS,
        summary="Create a standalone Draft invoice",
        description=_doc(
            f"""
Creates a **Draft**. Nothing is confirmed, no stock moves and the customer's
balance does not change until the Draft is confirmed.

**The server prices everything.** Send what is being sold, never what it costs.
For each line the server reads the product's price (or the variant's, when a
`variant_id` is sent), applies the unit conversion when a `unit_name` is sent,
then adds tax and totals. Sending `unit_price`, `total`, `status` or any other
server-owned field is a `400`.

**Body**

| Field | Required | Rules |
|---|---|---|
| `customer_id` | yes | A customer of this organization. |
| `warehouse_id` | yes | The warehouse the goods are supplied from. |
| `lines` | yes | 1 to {MAX_INVOICE_LINES} lines. See below. |
| `due_date` | no | A date. When omitted the server applies its default. |
| `discount` | no | Whole-invoice discount, a non-negative amount. Defaults to `0`. Cannot exceed the subtotal. Large discounts can require `discount_approval_id`. |
| `discount_approval_id` | no | An approved request that covers the discount. |
| `notes` | no | At most {MAX_NOTES_LENGTH} characters. May be blank or `null`. |
| `extra_data` | no | A JSON object for organization-specific fields. |
| `currency` | no | A three-letter code. Blank or `null` means the organization's currency. |

**Each line**

| Field | Required | Rules |
|---|---|---|
| `product_id` | yes | A product of this organization. |
| `quantity` | yes | A whole number from 1 to {MAX_LINE_QUANTITY}, counted in the line's unit. |
| `variant_id` | no | A variant of that product. Its price overrides the product's. |
| `unit_name` | no | A unit the product is sold in, for example `carton`, at most {MAX_UNIT_NAME_LENGTH} characters. Leave it out to sell in the product's base unit. A unit the product does not have is a `400`. |
| `description` | no | Custom line text, at most {MAX_LINE_TEXT_LENGTH} characters. The product name is used when empty. |

The same product, variant and unit can appear only once per invoice. Put
the total quantity on one line.

**Idempotency.** Send an `Idempotency-Key` header. Repeating the request with
the same key returns the first invoice instead of creating a second one.

**Response `201`:** the full invoice detail, with the server's prices and
totals. Show those, not your own arithmetic.
""",
            PERM_INVOICE_CREATE,
        ),
        request=InvoiceCreateSerializer,
        parameters=[_IDEMPOTENCY_HEADER],
        responses=_responses(
            status.HTTP_201_CREATED,
            InvoiceDetailSerializer,
            "The Draft.",
            conflicts=("APPROVAL_REQUIRED",),
        ),
        examples=[
            OpenApiExample(
                "Two cartons with a discount",
                value={
                    "customer_id": _CUSTOMER_ID,
                    "warehouse_id": _WAREHOUSE_ID,
                    "due_date": "2026-11-30",
                    "discount": "500.00",
                    "notes": "Postman test",
                    "lines": [{"product_id": _PRODUCT_ID, "quantity": 2, "unit_name": "carton"}],
                },
                request_only=True,
            ),
            OpenApiExample(
                "Minimal: one line in the base unit",
                value={
                    "customer_id": _CUSTOMER_ID,
                    "warehouse_id": _WAREHOUSE_ID,
                    "lines": [{"product_id": _PRODUCT_ID, "quantity": 5}],
                },
                request_only=True,
            ),
            OpenApiExample("The new Draft", value=_DRAFT, response_only=True, status_codes=["201"]),
            _ERROR_PRODUCT_NOT_FOUND,
            _ERROR_UNIT_NOT_AVAILABLE,
            _ERROR_DISCOUNT_TOO_BIG,
            _ERROR_SERVER_OWNED,
        ],
    ),
    update=extend_schema(
        tags=_TAGS,
        summary="Replace the lines of a Draft",
        description=_doc(
            f"""
Edits a **Draft** by sending its complete new set of lines. The server
re-prices the whole invoice. Only Drafts can be edited: any other status is a
`409` (`INVALID_INVOICE_TRANSITION`). To change a confirmed invoice, use
*Void and correct*.

This is a `PUT`: `PATCH` is not supported.

**Body**

* `lines` is **required** and is the *full* new set. A line you leave out is
  removed. The line rules are those of *Create*, including at most
  {MAX_INVOICE_LINES} lines and no duplicate product, variant and unit.
* Every other field is optional. A field you leave out **keeps its current
  value**: `due_date`, `discount`, `discount_approval_id`, `notes`.
* `discount` cannot be `null` here. Send `"0.00"` to remove it.
* `notes` cannot be `null` here. Send an empty string to clear it.
* `customer_id`, `warehouse_id` and `currency` cannot be changed on a Draft.
  Create a new Draft instead.

Server-owned fields are rejected exactly as in *Create*.

**Response `200`:** the full, re-priced invoice detail.
""",
            PERM_INVOICE_CREATE,
        ),
        request=InvoiceUpdateSerializer,
        responses=_responses(
            status.HTTP_200_OK,
            InvoiceDetailSerializer,
            "The re-priced Draft.",
            conflicts=("INVALID_INVOICE_TRANSITION", "APPROVAL_REQUIRED"),
        ),
        examples=[
            OpenApiExample(
                "Change the quantity and keep everything else",
                value={"lines": [{"product_id": _PRODUCT_ID, "quantity": 3, "unit_name": "carton"}]},
                request_only=True,
            ),
            _ERROR_TRANSITION,
            _ERROR_SERVER_OWNED,
        ],
    ),
    confirm=extend_schema(
        tags=_TAGS,
        summary="Confirm a Draft",
        description=_doc(
            """
Turns a Draft into a real invoice. It is **atomic**: either everything below
happens, or nothing does and the invoice stays a Draft.

1. The customer is locked and their credit limit is checked.
2. Stock is deducted from the invoice's warehouse. Quantities are counted in
   **base units** (2 cartons of 12 deduct 24), oldest batch first. Products that
   do not track inventory move no stock.
3. The totals are frozen. From now on the lines, prices and totals never change.
4. The customer's balance is updated and an audit event is written.

**Body.** Send `{}`. Both fields are optional:

* `credit_approval_id`: an approved credit override, needed only when the
  customer would exceed their credit limit and the organization's rules
  require approval.
* `payment`: money taken at confirmation (`amount`, `method`, optional
  `reference`, `paid_on`, `note`). **This is not available yet.** A request that
  includes a payment is refused with `503` before anything is confirmed, so the
  invoice is never confirmed without recording the money. Record the payment
  separately after confirming.

Confirming twice is a `409`, and never deducts stock a second time.

**Response `200`:** the confirmed invoice, with `confirmed_by` set and a new
`available_actions`.
""",
            PERM_INVOICE_CONFIRM,
        ),
        request=InvoiceConfirmSerializer,
        responses=_responses(
            status.HTTP_200_OK,
            InvoiceDetailSerializer,
            "The confirmed invoice.",
            conflicts=(
                "INVALID_INVOICE_TRANSITION",
                "INSUFFICIENT_STOCK",
                "CREDIT_LIMIT_EXCEEDED",
                "INVOICE_ALREADY_ISSUED_FOR_SALE",
                "APPROVAL_REQUIRED",
            ),
            extra={
                status.HTTP_503_SERVICE_UNAVAILABLE: OpenApiResponse(
                    description="A `payment` was sent, but recording a payment at confirmation is not available yet. Nothing was confirmed.",
                ),
            },
        ),
        examples=[
            OpenApiExample("Confirm", value={}, request_only=True),
            OpenApiExample(
                "Confirm with a credit approval",
                value={"credit_approval_id": "7e0d5c3a-2b14-4f68-9a07-1c8e6b4d2f53"},
                request_only=True,
            ),
            OpenApiExample("The confirmed invoice", value=_CONFIRMED, response_only=True, status_codes=["200"]),
            _ERROR_STOCK,
            _ERROR_TRANSITION,
        ],
    ),
    void=extend_schema(
        tags=_TAGS,
        summary="Void an invoice",
        description=_doc(
            f"""
Cancels an invoice. **The record stays forever** (PRD §44): a voided invoice
remains visible, with who voided it, when and why.

* A **reason is required**: at least {MIN_VOID_REASON_LENGTH} and at most
  {MAX_REASON_LENGTH} characters.
* An invoice that has **any payment** recorded cannot be voided
  (`INVOICE_HAS_PAYMENTS`). The money must be reversed through the payments
  flow first.
* Which statuses can be voided is decided by the server. Offer the action only
  when `available_actions` contains it.
* Voiding a confirmed invoice **returns its stock** to the same batches it came
  from. If the invoice was issued for a sale, the sale is cancelled too.
* The organization may require an **approval** for voids. Send its id as
  `approval_id`. An approval is consumed by the void and cannot be reused.

Voiding an already voided invoice is a `409`.

**Response `200`:** the voided invoice, with `voided_by`, `voided_at` and
`void_reason` set. To also create a corrected invoice in one step, use *Void and
correct*.
""",
            PERM_INVOICE_VOID,
        ),
        request=InvoiceVoidSerializer,
        responses=_responses(
            status.HTTP_200_OK,
            InvoiceDetailSerializer,
            "The voided invoice.",
            conflicts=("INVALID_INVOICE_TRANSITION", "INVOICE_HAS_PAYMENTS", "APPROVAL_REQUIRED"),
        ),
        examples=[
            OpenApiExample(
                "Void",
                value={"reason": "Customer cancelled the order"},
                request_only=True,
            ),
            OpenApiExample("The voided invoice", value=_VOIDED, response_only=True, status_codes=["200"]),
            _ERROR_TRANSITION,
        ],
    ),
    void_and_correct=extend_schema(
        tags=_TAGS,
        summary="Void an invoice and open a corrected Draft",
        description=_doc(
            f"""
The correction flow (PRD §24): voids the original and opens a new Draft in the
same transaction. If any part fails, **neither** happens: the original is not
voided and no Draft is created.

**Body**

| Field | Required | Rules |
|---|---|---|
| `reason` | yes | At least {MIN_VOID_REASON_LENGTH} and at most {MAX_REASON_LENGTH} characters. |
| `lines` | yes | The full set of lines for the corrected invoice. Same rules as *Create*. |
| `due_date` | no | A date. |
| `discount` | no | `null` or omitted keeps the **original's** discount. |
| `notes` | no | At most {MAX_NOTES_LENGTH} characters. May be `null`. |
| `approval_id` | no | Covers the **void** of the original. |
| `discount_approval_id` | no | Covers any discount on the **corrected** invoice. |

The void rules of *Void an invoice* apply to the original: payments block it,
and an approval may be required.

**Response `201`:** the **corrected Draft**, not the original. It links back
through `replaces`. Fetch the original to see it voided, with the new invoice
under `corrections`. Review the Draft, then confirm it like any other.
""",
            PERM_INVOICE_VOID,
        ),
        request=InvoiceVoidAndCorrectSerializer,
        responses=_responses(
            status.HTTP_201_CREATED,
            InvoiceDetailSerializer,
            "The corrected Draft.",
            conflicts=("INVALID_INVOICE_TRANSITION", "INVOICE_HAS_PAYMENTS", "APPROVAL_REQUIRED"),
        ),
        examples=[
            OpenApiExample(
                "Correct the quantity",
                value={
                    "reason": "Wrong quantity entered on the original invoice",
                    "lines": [{"product_id": _PRODUCT_ID, "quantity": 3, "unit_name": "carton"}],
                    "due_date": "2026-11-30",
                    "discount": "200.00",
                    "notes": "Corrected invoice",
                },
                request_only=True,
            ),
            OpenApiExample("The corrected Draft", value=_CORRECTED_DRAFT, response_only=True, status_codes=["201"]),
            _ERROR_TRANSITION,
        ],
    ),
    archive=extend_schema(
        tags=_TAGS,
        summary="Archive a settled invoice",
        description=_doc(
            f"""
Hides a settled invoice from the default list. **Only paid and voided
invoices** can be archived: anything else is a `409`.

Archiving never changes money or stock, and the invoice still counts in the
summary totals. It is **idempotent**: archiving an already archived invoice is
a `200` and keeps the original `archived_at`.

**Body.** Send `{{}}`, or an optional `reason` of at most {MAX_REASON_LENGTH}
characters. The reason goes to the audit log.

**Response `200`:** the invoice's new state only (`id`, `invoice_number`,
`status`, `status_display`, `archived_at`, `is_archived`, `available_actions`).
It is deliberately small. Update the row in your table from it, or reload the
detail if you need more.

Archived invoices reappear with `archived=archived` or `archived=all` on the
list.
""",
            PERM_INVOICE_ARCHIVE,
        ),
        request=InvoiceArchiveSerializer,
        responses=_responses(
            status.HTTP_200_OK,
            InvoiceStateSerializer,
            "The invoice's new state.",
            conflicts=("INVALID_INVOICE_TRANSITION",),
        ),
        examples=[
            OpenApiExample("Archive", value={"reason": "Year-end cleanup"}, request_only=True),
            OpenApiExample(
                "New state",
                value={
                    "id": _INVOICE_ID,
                    "invoice_number": "INV-000123",
                    "status": "void",
                    "status_display": "Void",
                    "archived_at": "2026-10-25T12:00:00+01:00",
                    "is_archived": True,
                    "available_actions": ["unarchive"],
                },
                response_only=True,
                status_codes=["200"],
            ),
            _ERROR_TRANSITION,
        ],
    ),
    unarchive=extend_schema(
        tags=_TAGS,
        summary="Bring an archived invoice back",
        description=_doc(
            f"""
Returns an archived invoice to the default list. It never changes money or
stock.

**Body.** Same shape as *Archive*: `{{}}`, or an optional `reason` of at most
{MAX_REASON_LENGTH} characters that goes to the audit log.

**Response `200`:** the invoice's new state, with `is_archived` set to `false`
and `archived_at` set to `null`.
""",
            PERM_INVOICE_ARCHIVE,
        ),
        request=InvoiceUnarchiveSerializer,
        responses=_responses(status.HTTP_200_OK, InvoiceStateSerializer, "The invoice's new state."),
        examples=[OpenApiExample("Unarchive", value={}, request_only=True)],
    ),
)
