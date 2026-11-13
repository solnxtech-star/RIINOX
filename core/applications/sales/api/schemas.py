from core.applications.warehouse.api.schemas import LOCATION_ACCESS_NOTE
from core.applications.warehouse.api.schemas import LOCATION_FILTER_PARAM
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
    extend_schema_view,
    inline_serializer,
)
from rest_framework import serializers

from core.applications.sales.api.serializers import (
    SaleCancelSerializer,
    SaleCreateSerializer,
    SaleReturnCreateSerializer,
    SaleReturnSerializer,
    SaleSerializer,
)

# ---------------------------------------------------------------------------
# Reusable Error Serializers & Status Responses
# ---------------------------------------------------------------------------

DetailErrorSerializer = inline_serializer(
    name="SalesDetailError",
    fields={"detail": serializers.CharField(help_text="Human-readable error description.")},
)

CodeDetailErrorSerializer = inline_serializer(
    name="SalesCodeDetailError",
    fields={
        "code": serializers.CharField(help_text="Machine-readable error identifier."),
        "detail": serializers.CharField(help_text="Detailed explanation of the validation or business rule failure."),
    },
)

UNAUTHENTICATED_RESPONSE = OpenApiResponse(
    response=DetailErrorSerializer,
    description="Authentication credentials were not provided or are invalid.",
    examples=[
        OpenApiExample(
            "Unauthenticated",
            value={"detail": "Authentication credentials were not provided."},
            response_only=True,
        )
    ],
)

FORBIDDEN_RESPONSE = OpenApiResponse(
    response=DetailErrorSerializer,
    description="The caller lacks permission to perform this action (e.g. lacking VIEW_SALES, CREATE_SALE, CREATE_SALE_RETURN, or APPLY_DISCOUNT).",
    examples=[
        OpenApiExample(
            "Forbidden",
            value={"detail": "You do not have permission to perform this action."},
            response_only=True,
        )
    ],
)

PLAN_LIMIT_EXCEEDED_RESPONSE = OpenApiResponse(
    response=CodeDetailErrorSerializer,
    description="The organization subscription is inactive or has reached plan limits (e.g. max_transactions_per_month).",
    examples=[
        OpenApiExample(
            "Monthly Transaction Limit Reached",
            value={
                "code": "PLAN_LIMIT_EXCEEDED",
                "detail": "Plan limit reached for 'max_transactions_per_month'. Upgrade your subscription to continue processing sales.",
            },
            response_only=True,
        ),
        OpenApiExample(
            "Subscription Inactive",
            value={
                "code": "SUBSCRIPTION_INACTIVE",
                "detail": "Organization subscription is expired or inactive.",
            },
            response_only=True,
        ),
    ],
)

NOT_FOUND_RESPONSE = OpenApiResponse(
    response=DetailErrorSerializer,
    description="The requested sale or return record was not found, or belongs to another organization.",
    examples=[
        OpenApiExample(
            "NotFound",
            value={"detail": "Not found."},
            response_only=True,
        )
    ],
)

# ---------------------------------------------------------------------------
# Request & Response Examples with Valid UUIDs
# ---------------------------------------------------------------------------

_sale_create_example = OpenApiExample(
    "Standard Sale Order Request",
    summary="Create sale with line items and FIFO inventory deduction",
    description=(
        "Standard sales order creation payload. Links customer, optional warehouse/location, and sales rep. "
        "If location/warehouse is omitted, the backend automatically defaults to the organization's active warehouse. "
        "Line items reference catalog products. Deducts inventory using FIFO and creates ledger entries."
    ),
    value={
        "customer": "b2c3d4e5-f6a7-4789-b1c2-d3e4f5a6b7c9",
        "warehouse": "c3d4e5f6-a7b8-4789-b1c2-d3e4f5a6b7cb",
        "location": "c3d4e5f6-a7b8-4789-b1c2-d3e4f5a6b7ca",
        "sales_rep": "11111111-2222-3333-4444-555555555555",
        "currency": "NGN",
        "reference_number": "SO-2026-1042",
        "due_date": "2026-10-31",
        "billing_address": "12 Marina Boulevard, Lagos",
        "shipping_address": "45 Commercial Avenue, Ikeja, Lagos",
        "discount_amount": "5000.00",
        "notes": "Corporate order for regional branch supply.",
        "payment": {
            "method": "cash",
            "amount": "50000.00",
            "reference": "POS-CASH-001",
        },
        "items": [
            {
                "product": "d1e2f3a4-b5c6-4789-b1c2-d3e4f5a6b7c8",
                "quantity": 10,
                "unit_name": "drum_20l",
                "unit_price": "5000.00",
                "discount": "500.00",
            }
        ],
    },
    request_only=True,
)

_sale_response_example = OpenApiExample(
    "Sale Representation",
    summary="Complete sale record",
    description="Full representation of an executed sale including line items and total amounts.",
    value={
        "id": "e4f5a6b7-c8d9-4789-b1c2-d3e4f5a6b7cb",
        "sale_id": "SALE-2026-0089",
        "organization": "a1b2c3d4-e5f6-4789-b1c2-d3e4f5a6b7c8",
        "customer": "b2c3d4e5-f6a7-4789-b1c2-d3e4f5a6b7c9",
        "warehouse": "c3d4e5f6-a7b8-4789-b1c2-d3e4f5a6b7cb",
        "location": "c3d4e5f6-a7b8-4789-b1c2-d3e4f5a6b7ca",
        "sales_rep": "11111111-2222-3333-4444-555555555555",
        "payment_status": "paid",
        "fulfillment_status": "fulfilled",
        "currency": "NGN",
        "reference_number": "SO-2026-1042",
        "due_date": "2026-10-31",
        "billing_address": "12 Marina Boulevard, Lagos",
        "shipping_address": "45 Commercial Avenue, Ikeja, Lagos",
        "subtotal": "50000.00",
        "discount_amount": "5000.00",
        "tax_amount": "3375.00",
        "prices_include_tax": False,
        "total": "48375.00",
        "change_due": "1625.00",
        "notes": "Corporate order for regional branch supply.",
        "items": [
            {
                "id": "f5a6b7c8-d9e0-4789-b1c2-d3e4f5a6b7cc",
                "sale": "e4f5a6b7-c8d9-4789-b1c2-d3e4f5a6b7cb",
                "product": "d1e2f3a4-b5c6-4789-b1c2-d3e4f5a6b7c8",
                "variant": None,
                "product_name": "Premium Industrial Solvent",
                "product_sku": "SOLV-IND-01",
                "quantity": 10,
                "returned_quantity": 0,
                "unit_name": "drum_20l",
                "unit_multiplier": 1.0,
                "unit_price": "5000.00",
                "discount": "500.00",
                "discount_type": "fixed",
                "tax": "337.50",
                "tax_rate": "7.50",
                "total": "4837.50",
            }
        ],
        "created_at": "2026-10-12T10:00:00Z",
    },
    response_only=True,
)

_sale_return_create_example = OpenApiExample(
    "Sale Return Request",
    summary="Process customer return with restock or damage action",
    description="Registers returned goods against an existing sale. If restock_action is return_to_stock, restocks inventory and writes ledger entries.",
    value={
        "original_sale": "e4f5a6b7-c8d9-4789-b1c2-d3e4f5a6b7cb",
        "organization": "a1b2c3d4-e5f6-4789-b1c2-d3e4f5a6b7c8",
        "return_reason": "DEFECTIVE",
        "items": [
            {
                "sale_item": "f5a6b7c8-d9e0-4789-b1c2-d3e4f5a6b7cc",
                "quantity": 1,
                "condition": "defective",
            }
        ],
    },
    request_only=True,
)

_sale_return_response_example = OpenApiExample(
    "Sale Return Response",
    summary="Confirmed return record",
    description="Full representation of processed return including status and returned items.",
    value={
        "id": "a7b8c9d0-e1f2-4789-b1c2-d3e4f5a6b7cd",
        "original_sale": "e4f5a6b7-c8d9-4789-b1c2-d3e4f5a6b7cb",
        "organization": "a1b2c3d4-e5f6-4789-b1c2-d3e4f5a6b7c8",
        "return_reason": "DEFECTIVE",
        "refund_amount": "4837.50",
        "refund_method": "original_payment",
        "status": "completed",
        "items": [
            {
                "id": "b8c9d0e1-f2a3-4789-b1c2-d3e4f5a6b7ce",
                "sale_return": "a7b8c9d0-e1f2-4789-b1c2-d3e4f5a6b7cd",
                "sale_item": "f5a6b7c8-d9e0-4789-b1c2-d3e4f5a6b7cc",
                "quantity": 1,
                "refund_amount": "4837.50",
                "condition": "defective",
                "restock_action": "return_to_stock",
            }
        ],
    },
    response_only=True,
)

# ---------------------------------------------------------------------------
# Sale ViewSet Schema Decorator
# ---------------------------------------------------------------------------

sale_viewset_schema = extend_schema_view(
    list=extend_schema(
        tags=["Sales"],
        summary="List Sales",
        description=(
            "### Overview\n"
            "Retrieves a paginated list of sales records for organizations the authenticated user belongs to. "
            "Implements strict multi-tenant boundary isolation (PRD §6).\n\n"
            "### Required Permissions & Access Control (PRD §28, §55)\n"
            "- Requires **`VIEW_SALES`** permission in the target organization.\n"
            "- Unauthenticated requests return HTTP 401.\n"
            "- Authenticated callers without active membership or `VIEW_SALES` capability receive HTTP 403 Forbidden.\n\n"
            "### Multi-Tenancy & Tenant Boundaries (PRD §6)\n"
            "- Scoped to the organization in the URL path.\n"
            "- Filter one branch with `?location=<warehouse id>`.\n\n"
            + LOCATION_ACCESS_NOTE + "\n\n"
            "### Plan Limits & Subscription Invariants (PRD §10, §52)\n"
            "- Read operations require active tenant organization standing.\n"
            "- Querying sales records is read-only and does NOT consume monthly transaction allowances (`max_transactions_per_month`)."
        ),
        parameters=[LOCATION_FILTER_PARAM],
        responses={
            200: OpenApiResponse(response=SaleSerializer(many=True), description="Successful retrieval of sales list."),
            401: UNAUTHENTICATED_RESPONSE,
            403: FORBIDDEN_RESPONSE,
        },
    ),
    retrieve=extend_schema(
        tags=["Sales"],
        summary="Retrieve Sale",
        description=(
            "### Overview\n"
            "Fetches detailed information about a specific sale by UUID, including all line items, financial totals, "
            "payment status, and fulfillment state.\n\n"
            "### Required Permissions & Access Control (PRD §28, §55)\n"
            "- Requires **`VIEW_SALES`** permission in the target tenant organization.\n"
            "- Cross-tenant lookups return HTTP 404 to avoid entity enumeration across tenant boundaries."
        ),
        responses={
            200: OpenApiResponse(
                response=SaleSerializer,
                description="Successful retrieval of sale details.",
                examples=[_sale_response_example],
            ),
            401: UNAUTHENTICATED_RESPONSE,
            403: FORBIDDEN_RESPONSE,
            404: NOT_FOUND_RESPONSE,
        },
    ),
    create=extend_schema(
        tags=["Sales"],
        summary="Create Sale (Fulfill & Deduct Stock)",
        description=(
            "### Overview\n"
            "Creates a new sale record along with its line items. Triggers atomic inventory deduction across batches, "
            "creates immutable ledger entries, and records unified business transactions (PRD §16, §26, §42).\n\n"
            "### Warehouse & Stock Location Resolution\n"
            "- **`warehouse`** (UUID, optional): Target warehouse fulfilling the order.\n"
            "- **`location`** (UUID, optional): Specific shelf/bin stock location within the warehouse.\n"
            "- **Automatic fallback**: both fields are optional for members with access to all locations. "
            "If omitted, the backend uses the organization's **default location** (`is_default`), or the first active "
            "location if the default is inactive, and that location's first active storage area.\n"
            "- **Location access (PRD §35)**: the resulting location (the storage area's location if `location` is sent, "
            "else `warehouse`) must be one of the caller's locations, otherwise `403 LOCATION_ACCESS_DENIED`. "
            "Members limited to specific locations **must** send `warehouse` or `location` "
            "(`400 LOCATION_REQUIRED`). Tip: pre-fill it from the branch switcher.\n\n"
            "### Payment Tender & Cash Change Due (PRD §39)\n"
            "- **Auto 'Pay in Full' (Omitted `amount`)**: If `payment.amount` is omitted or null, the backend automatically records the full outstanding net total as paid without requiring frontend calculation.\n"
            "- **Cash Overpayment & Change Due**: When paying via `cash`, if tendered `amount > total`, the backend records the exact net sale total as received revenue and returns the remaining customer change in `change_due` (e.g. ₦1,000 tendered on a ₦950 sale records ₦950 and returns `change_due: \"50.00\"`).\n"
            "- **Partial Payments**: If tendered `amount < total`, the payment records the partial amount and sets `payment_status: \"partially_paid\"`.\n"
            "- **Non-Cash Overpayment Guard**: Non-cash payment methods (`card`, `bank_transfer`, `pos`) strictly reject overpayments exceeding the payable total with HTTP 400.\n\n"
            "### Tax (PRD §13, §39): calculated by the server\n"
            "- **Never send tax.** A non-zero `tax_amount`, or `tax` / `tax_rate` on an item, is a `400`. "
            "Configure tax instead on the product (`tax_config`, `tax_rate`) and the organization "
            "(`default_tax_rate`, `prices_include_tax`).\n"
            "- Each line is taxed on its amount after discounts at the product's rate (or the organization's "
            "default; exempt / non-taxable products at 0), rounded per line; `tax_amount` is the sum.\n"
            "- The response's `prices_include_tax` tells how to read it: `true` = tax is **inside** the prices "
            "(total = subtotal - discount; print \"VAT included\"), `false` = tax is **added** "
            "(total = subtotal - discount + tax_amount).\n"
            "- Selling on credit beyond the customer's `credit_limit` needs the `APPROVE_REQUESTS` permission; "
            "otherwise `403 CREDIT_LIMIT_EXCEEDED`.\n\n"
            "### Required Permissions & Access Control (PRD §28, §55)\n"
            "- **`CREATE_SALE`**: Required to create sales orders.\n"
            "- **`APPLY_DISCOUNT`**: If `discount_amount > 0` or any line item specifies `discount > 0`, the caller "
            "MUST possess `APPLY_DISCOUNT` permission in the organization. Users lacking this permission (such as standard "
            "Sales Representatives) are rejected with HTTP 400 (`You do not have permission to apply discounts in this organization.`).\n\n"
            "### Plan Limits & Subscription Invariants (PRD §10, §52)\n"
            "- **Active Subscription**: Calls `ensure_subscription_active(organization)`. Inactive or delinquent subscriptions "
            "reject order creation with HTTP 402/403 (`SUBSCRIPTION_INACTIVE`).\n"
            "- **Monthly Transactions Quota**: Creates a centralized system `Transaction` (type `SALE`, status `COMPLETED`), "
            "consuming transaction allowance via `consume_usage(organization, UsageMetric.TRANSACTIONS)`. If the monthly transaction "
            "limit is exceeded, order creation is rejected with HTTP 403 (`PLAN_LIMIT_REACHED`).\n\n"
            "### Inventory & Ledger Invariants (PRD §16, §26, §42)\n"
            "When this endpoint executes inside its `@transaction.atomic` block:\n"
            "1. **Row-Level Locking**: Acquires `select_for_update()` locks on warehouse `Inventory` rows to eliminate race conditions.\n"
            "2. **FIFO Batch Deductions**: Sorts batches by `expiry_date ASC (nulls_last=True)` and `created_at ASC`. Depletes earliest expiring batches first.\n"
            "3. **Strict Stock Tracking (`track_inventory`)**:\n"
            "   - When `product.track_inventory == True`: validates total available stock >= requested quantity. If insufficient stock, "
            "aborts with HTTP 400 (`Out of stock: {product.name}...`).\n"
            "   - When `product.track_inventory == False`: allows stock to go negative for services, untracked items, or backorders.\n"
            "4. **Packaging Multipliers**: Automatically calculates base unit quantities using `unit_multiplier`.\n"
            "5. **Immutable Ledger**: Generates an `InventoryLedgerEntry` for every deducted batch (`quantity_moved = -qty`, `unit_cost = product.purchase_cost`).\n"
            "6. **Audit Trail**: Dispatches audit events `SALES_ORDER_CREATED` and `SALES_ORDER_FULFILLED`."
        ),
        request=SaleCreateSerializer,
        examples=[_sale_create_example],
        responses={
            201: OpenApiResponse(
                response=SaleSerializer,
                description="Sale created successfully, inventory deducted, and ledger updated.",
                examples=[_sale_response_example],
            ),
            400: OpenApiResponse(
                response=CodeDetailErrorSerializer,
                description="Validation failure (e.g. out of stock for tracked products or unauthorized discount).",
                examples=[
                    OpenApiExample(
                        "Out of Stock",
                        value={"detail": "Out of stock: Widget Pro. The cashier requested 5, but the system only has 2 available. Please restock or do a physical count."},
                        response_only=True,
                    ),
                    OpenApiExample(
                        "Unauthorized Discount",
                        value={"discount_amount": "You do not have permission to apply discounts in this organization."},
                        response_only=True,
                    ),
                ],
            ),
            401: UNAUTHENTICATED_RESPONSE,
            402: PLAN_LIMIT_EXCEEDED_RESPONSE,
            403: FORBIDDEN_RESPONSE,
        },
    ),
    update=extend_schema(
        tags=["Sales"],
        summary="Update Sale",
        description=(
            "### Overview\n"
            "Updates an existing sale record entirely.\n\n"
            "### Required Permissions & Access Control (PRD §28, §55)\n"
            "- Requires **`CREATE_SALE`** permission.\n"
            "- Requires **`APPLY_DISCOUNT`** permission if modified discounts are greater than zero.\n\n"
            "### Plan Limits & Subscription Invariants (PRD §10, §52)\n"
            "- Evaluates active subscription standing (`ensure_subscription_active`)."
        ),
        request=SaleCreateSerializer,
        responses={
            200: OpenApiResponse(response=SaleSerializer, description="Sale updated successfully."),
            400: OpenApiResponse(response=DetailErrorSerializer, description="Validation error."),
            401: UNAUTHENTICATED_RESPONSE,
            402: PLAN_LIMIT_EXCEEDED_RESPONSE,
            403: FORBIDDEN_RESPONSE,
            404: NOT_FOUND_RESPONSE,
        },
    ),
    partial_update=extend_schema(
        tags=["Sales"],
        summary="Partial Update Sale",
        description=(
            "### Overview\n"
            "Partially updates specific attributes of an existing sale record.\n\n"
            "### Required Permissions & Access Control (PRD §28, §55)\n"
            "- Requires **`CREATE_SALE`** permission.\n"
            "- Requires **`APPLY_DISCOUNT`** permission if updating discounts.\n\n"
            "### Plan Limits & Subscription Invariants (PRD §10, §52)\n"
            "- Evaluates active subscription standing (`ensure_subscription_active`)."
        ),
        request=SaleCreateSerializer,
        responses={
            200: OpenApiResponse(response=SaleSerializer, description="Sale partially updated successfully."),
            400: OpenApiResponse(response=DetailErrorSerializer, description="Validation error."),
            401: UNAUTHENTICATED_RESPONSE,
            402: PLAN_LIMIT_EXCEEDED_RESPONSE,
            403: FORBIDDEN_RESPONSE,
            404: NOT_FOUND_RESPONSE,
        },
    ),
    destroy=extend_schema(
        tags=["Sales"],
        summary="Delete Sale",
        description=(
            "### Overview\n"
            "Deletes an unfulfilled sale record.\n\n"
            "### Required Permissions & Access Control (PRD §28, §55)\n"
            "- Requires **`CREATE_SALE`** permission.\n\n"
            "### Data & Ledger Integrity Warning\n"
            "Sales that have moved inventory and created immutable ledger entries should be reversed using the "
            "Sale Return workflow (`POST /api/sales/returns/`) rather than hard deleted, preserving financial and inventory audit trails."
        ),
        responses={
            204: OpenApiResponse(description="Sale deleted successfully."),
            401: UNAUTHENTICATED_RESPONSE,
            403: FORBIDDEN_RESPONSE,
            404: NOT_FOUND_RESPONSE,
        },
    ),
    cancel=extend_schema(
        tags=["Sales"],
        summary="Cancel Sale Order",
        description=(
            "### Overview\n"
            "Cancels an existing sale order per PRD §44 (Cancellations & Immutability).\n\n"
            "### Business Workflow\n"
            "- Validates active organization subscription.\n"
            "- Verifies the sale is not already cancelled.\n"
            "- Reverses all deducted stock movements back to warehouse inventory.\n"
            "- Records compensating ledger entries (`InventoryLedgerEntry`).\n"
            "- Updates `cancelled_at`, `cancelled_by`, `cancel_reason`, and sets `fulfillment_status = CANCELED`.\n"
            "- Records centralized audit log event (`sales_order.cancelled`)."
        ),
        request=SaleCancelSerializer,
        responses={
            200: OpenApiResponse(response=SaleSerializer, description="Sale cancelled successfully and stock reversed."),
            400: OpenApiResponse(response=DetailErrorSerializer, description="Sale cannot be cancelled (already cancelled or has returns)."),
            401: UNAUTHENTICATED_RESPONSE,
            403: FORBIDDEN_RESPONSE,
            404: NOT_FOUND_RESPONSE,
        },
    ),
)

# ---------------------------------------------------------------------------
# SaleReturn ViewSet Schema Decorator
# ---------------------------------------------------------------------------

sale_return_viewset_schema = extend_schema_view(
    list=extend_schema(
        tags=["Sales"],
        summary="List Sale Returns",
        description=(
            "### Overview\n"
            "Retrieves a paginated list of all sale returns for organizations the authenticated user belongs to. "
            "Implements strict multi-tenant boundary isolation (PRD §6).\n\n"
            "### Required Permissions & Access Control (PRD §28, §55)\n"
            "- Requires **`VIEW_SALES`** permission in the target tenant organization.\n"
            "- Unauthenticated callers receive HTTP 401; unauthorized callers receive HTTP 403 Forbidden.\n\n"
            "### Plan Limits & Subscription Invariants (PRD §10, §52)\n"
            "- Read operations do NOT consume monthly transaction allowances (`max_transactions_per_month`).\n\n"
            "### Location access\n"
            "A return belongs to the location of its original sale; `?location=` filters by it.\n\n"
            + LOCATION_ACCESS_NOTE
        ),
        parameters=[LOCATION_FILTER_PARAM],
        responses={
            200: OpenApiResponse(response=SaleReturnSerializer(many=True), description="Successful retrieval of returns list."),
            401: UNAUTHENTICATED_RESPONSE,
            403: FORBIDDEN_RESPONSE,
        },
    ),
    retrieve=extend_schema(
        tags=["Sales"],
        summary="Retrieve Sale Return",
        description=(
            "### Overview\n"
            "Retrieves detailed information about a specific sale return by UUID, including refund totals and return line items.\n\n"
            "### Required Permissions & Access Control (PRD §28, §55)\n"
            "- Requires **`VIEW_SALES`** permission in the target tenant organization.\n"
            "- Cross-tenant lookups return HTTP 404 to protect tenant boundaries."
        ),
        responses={
            200: OpenApiResponse(
                response=SaleReturnSerializer,
                description="Successful retrieval of return details.",
                examples=[_sale_return_response_example],
            ),
            401: UNAUTHENTICATED_RESPONSE,
            403: FORBIDDEN_RESPONSE,
            404: NOT_FOUND_RESPONSE,
        },
    ),
    create=extend_schema(
        tags=["Sales"],
        summary="Create Sale Return (Restock & Refund)",
        description=(
            "### Overview\n"
            "Creates a new customer return against an original sale, specifying items returned, refund amounts, "
            "item conditions, and restock actions (PRD §16, §26).\n\n"
            "### Required Permissions & Access Control (PRD §28, §55)\n"
            "- Requires **`CREATE_SALE_RETURN`** permission in the target organization.\n"
            "- Roles without return authorization (e.g. standard Sales Representatives) receive HTTP 403 Forbidden.\n"
            "- If `organization` is omitted from the request body, tenant scope is automatically resolved from `original_sale.organization`.\n"
            "- The original sale's location must be one of the caller's locations (`403 LOCATION_ACCESS_DENIED`).\n\n"
            "### Plan Limits & Subscription Invariants (PRD §10, §52)\n"
            "- **Active Subscription**: Calls `ensure_subscription_active(organization)`. Inactive subscriptions reject return creation.\n"
            "- **Monthly Transactions Quota**: Creates a centralized system `Transaction` (type `RETURN`, status `COMPLETED`), "
            "consuming transaction allowance via `consume_usage(organization, UsageMetric.TRANSACTIONS)`. Exceeding limits returns HTTP 403 (`PLAN_LIMIT_REACHED`).\n\n"
            "### Inventory & Ledger Invariants (PRD §16, §26)\n"
            "When processed atomically via `process_return_inventory_and_ledger`:\n"
            "1. **Restocking Action (`restock_action`)**:\n"
            "   - `return_to_stock`: Adds returned stock back into the warehouse generic pool (`batch=None`), increases `Inventory.quantity`, "
            "and creates a positive `InventoryLedgerEntry` (`quantity_moved = +returned_qty`).\n"
            "   - `damage`: Does not return stock to sellable inventory; preserves defective status for damage write-off.\n"
            "2. **Audit Attribution**: Records audit logs linking the return to the original sale and sales rep."
        ),
        request=SaleReturnCreateSerializer,
        examples=[_sale_return_create_example],
        responses={
            201: OpenApiResponse(
                response=SaleReturnSerializer,
                description="Sale return processed successfully, stock restored, and ledger updated.",
                examples=[_sale_return_response_example],
            ),
            400: OpenApiResponse(response=CodeDetailErrorSerializer, description="Validation failure or invalid return items."),
            401: UNAUTHENTICATED_RESPONSE,
            402: PLAN_LIMIT_EXCEEDED_RESPONSE,
            403: FORBIDDEN_RESPONSE,
        },
    ),
    update=extend_schema(
        tags=["Sales"],
        summary="Update Sale Return",
        description=(
            "### Overview\n"
            "Updates an existing sale return completely.\n\n"
            "### Required Permissions & Access Control (PRD §28, §55)\n"
            "- Requires **`CREATE_SALE_RETURN`** permission.\n\n"
            "### Plan Limits & Subscription Invariants (PRD §10, §52)\n"
            "- Evaluates active subscription standing (`ensure_subscription_active`)."
        ),
        request=SaleReturnCreateSerializer,
        responses={
            200: OpenApiResponse(response=SaleReturnSerializer, description="Sale return updated successfully."),
            400: OpenApiResponse(response=DetailErrorSerializer, description="Validation error."),
            401: UNAUTHENTICATED_RESPONSE,
            402: PLAN_LIMIT_EXCEEDED_RESPONSE,
            403: FORBIDDEN_RESPONSE,
            404: NOT_FOUND_RESPONSE,
        },
    ),
    partial_update=extend_schema(
        tags=["Sales"],
        summary="Partial Update Sale Return",
        description=(
            "### Overview\n"
            "Partially updates an existing sale return.\n\n"
            "### Required Permissions & Access Control (PRD §28, §55)\n"
            "- Requires **`CREATE_SALE_RETURN`** permission.\n\n"
            "### Plan Limits & Subscription Invariants (PRD §10, §52)\n"
            "- Evaluates active subscription standing (`ensure_subscription_active`)."
        ),
        request=SaleReturnCreateSerializer,
        responses={
            200: OpenApiResponse(response=SaleReturnSerializer, description="Sale return partially updated successfully."),
            400: OpenApiResponse(response=DetailErrorSerializer, description="Validation error."),
            401: UNAUTHENTICATED_RESPONSE,
            402: PLAN_LIMIT_EXCEEDED_RESPONSE,
            403: FORBIDDEN_RESPONSE,
            404: NOT_FOUND_RESPONSE,
        },
    ),
    destroy=extend_schema(
        tags=["Sales"],
        summary="Delete Sale Return",
        description=(
            "### Overview\n"
            "Deletes a sale return record.\n\n"
            "### Required Permissions & Access Control (PRD §28, §55)\n"
            "- Requires **`CREATE_SALE_RETURN`** permission."
        ),
        responses={
            204: OpenApiResponse(description="Sale return deleted successfully."),
            401: UNAUTHENTICATED_RESPONSE,
            403: FORBIDDEN_RESPONSE,
            404: NOT_FOUND_RESPONSE,
        },
    ),
)
