from drf_spectacular.utils import (
    extend_schema,
    extend_schema_view,
    OpenApiExample,
    OpenApiParameter,
    OpenApiTypes,
    OpenApiResponse,
)
from core.applications.inventory.api.serializers import InventorySummarySerializer
from core.applications.inventory.api.serializers import StockActionErrorSerializer
from core.applications.inventory.api.serializers import StockActionResultSerializer
from core.applications.inventory.api.serializers import StockAdjustmentRejectSerializer
from core.applications.inventory.api.serializers import StockTransferSerializer
from core.applications.warehouse.api.schemas import LOCATION_ACCESS_NOTE
from core.applications.warehouse.api.schemas import LOCATION_FILTER_PARAM

# ---------------------------------------------------------------------------
# Query parameters
# The organization always comes from the URL path; there is no organization
# query parameter or header on these endpoints.
# ---------------------------------------------------------------------------
PRODUCT_FILTER_PARAM = OpenApiParameter(
    "product",
    OpenApiTypes.UUID,
    OpenApiParameter.QUERY,
    required=False,
    description="Only rows of this product. Malformed id: 400.",
)

# ---------------------------------------------------------------------------
# Request Examples
# ---------------------------------------------------------------------------
stock_adjustment_create_example = OpenApiExample(
    "Request Stock Adjustment",
    summary="Create a new stock adjustment request",
    description="""Requests an inventory balance adjustment (loss, damage, count variance, found stock).

### Workflow & Invariant Architecture (PRD §15, §16):
- Creates a `StockAdjustmentRequest`.
- **Auto-Approval**: If the authenticated user is an **Admin or Owner** (`UsersRole.ADMIN`, `UsersRole.OWNER`, or superuser), the request is automatically approved in the same atomic transaction: status becomes `APPROVED`, physical stock is updated in `Inventory`, an immutable `InventoryLedgerEntry` is written, and an `INVENTORY_ADJUSTED` audit event is dispatched.
- **Pending Review**: For non-privileged roles (Manager, Sales Rep, Staff), the request enters `PENDING` status awaiting review by an authorized administrator via the `/approve` endpoint.

### Required Permissions & Access Control (PRD §28, §55):
- Requires `REQUEST_STOCK_ADJUSTMENT` to submit.
- Requires `APPROVE_STOCK_ADJUSTMENT` or `APPROVE_REQUESTS` to approve.
- Scoped strictly to the caller's active organization.

### Plan Limits & Subscription Invariants (PRD §10, §52):
- **Subscription Standing**: Enforces `ensure_subscription_active(product.organization)`. The tenant must be in good standing (`ACTIVE` or `TRIALING`). Inactive subscriptions reject creation with `SUBSCRIPTION_REQUIRED` (HTTP 402).
- **Monthly Transactions Limit**: When approved, records a system `Transaction` (`transaction_type = 'ADJUSTMENT'`) and consumes allowance via `consume_usage(organization, UsageMetric.TRANSACTIONS)`.
- **Locations Limit**: The target warehouse must be an active facility within the tenant's `max_locations` plan quota.""",
    value={
        "product": "d290f1ee-6c54-4b01-90e6-d701748f0851",
        "warehouse": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
        "requested_quantity_change": -5,
        "adjustment_reason": "damaged",
        "notes": "Water leakage in aisle 3 damaged packaging."
    },
    request_only=True
)

stock_transfer_example = OpenApiExample(
    "Transfer Stock",
    summary="Transfer stock between branches/warehouses",
    description="""Atomically moves inventory from a source warehouse to a destination warehouse.

### Workflow & Invariant Architecture (PRD §15, §16):
- Atomically decrements stock in `source_warehouse_id` and increments stock in `destination_warehouse_id`.
- Creates two immutable `InventoryLedgerEntry` records (`-quantity` at source, `+quantity` at destination) tied to a single `TRANSFER` transaction.
- Prevents transfers if available source stock is insufficient.

### Required Permissions & Access Control (PRD §28):
- Requires `TRANSFER_STOCK` permission.
- Both `source_warehouse_id` and `destination_warehouse_id` must belong to the caller's active tenant organization. Cross-tenant transfers are strictly blocked.

### Plan Limits & Subscription Invariants (PRD §10, §52):
- **Subscription Standing**: Requires active subscription standing (`ensure_subscription_active`).
- **Monthly Transactions Limit**: The generated transfer transaction counts toward `max_transactions_per_month`.
- **Locations Quota**: Both source and destination warehouses are governed by the tenant's `max_locations` plan limit.""",
    value={
        "product_id": "d290f1ee-6c54-4b01-90e6-d701748f0851",
        "source_warehouse_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
        "destination_warehouse_id": "184d0b2b-6c58-47bc-9b63-1d01f11a8c88",
        "quantity": 50
    },
    request_only=True
)

# ---------------------------------------------------------------------------
# Schema Views
# ---------------------------------------------------------------------------
stock_adjustment_approve_schema = extend_schema(
    tags=['Inventory'],
    summary="Approve Stock Adjustment Request",
    description="""Approves a pending stock adjustment request, modifying inventory balances and recording immutable ledger movements.

### Workflow & Invariant Architecture (PRD §15, §16):
- Transitions request status from `PENDING` to `APPROVED`. Sets `reviewed_by` and `reviewed_at`.
- Updates `Inventory.quantity` by `requested_quantity_change`.
- Creates an immutable `InventoryLedgerEntry` recording the stock delta.
- Emits an `INVENTORY_ADJUSTED` audit log entry.

### Permission Requirements (PRD §28):
- Requires `APPROVE_STOCK_ADJUSTMENT` or `APPROVE_REQUESTS` (Admin or Owner role). Unauthorized users receive HTTP 403 Forbidden.
- The request must belong to this organization and one of the caller's locations; otherwise 404.
- Approving twice at the same time is safe: the request row is locked, the second call gets 400.

### Plan Limits (PRD §10):
- Records a system `Transaction` (`transaction_type = 'ADJUSTMENT'`) bounded by the `max_transactions_per_month` plan quota.

### Request body
**None.** The adjustment is identified by the `id` in the URL; any body is ignored.

To turn a request down instead, use `POST .../adjustments/{id}/reject/`.""",
    request=None,
    responses={
        200: OpenApiResponse(
            response=StockActionResultSerializer,
            description="Approved: stock, ledger and audit log are updated.",
            examples=[OpenApiExample("Approved", value={"status": "approved"}, response_only=True)],
        ),
        400: OpenApiResponse(
            response=StockActionErrorSerializer,
            description="The request is no longer PENDING (already approved or rejected).",
            examples=[
                OpenApiExample(
                    "Not pending",
                    value={"code": "APPROVAL_ERROR", "detail": "Only PENDING requests can be approved."},
                    response_only=True,
                )
            ],
        ),
        403: OpenApiResponse(description="The caller lacks `APPROVE_STOCK_ADJUSTMENT`."),
        404: OpenApiResponse(
            description="No such request in this organization, or it is at a location the caller can't access."
        ),
    },
)

stock_adjustment_reject_schema = extend_schema(
    tags=['Inventory'],
    summary="Reject Stock Adjustment Request",
    description="""Turns down a pending stock adjustment request.

### What happens
- Status goes from `PENDING` to `REJECTED`; `reviewed_by`, `reviewed_at` and `rejection_reason` are set.
- **Stock is not touched**: no inventory change, no ledger entry, no transaction.
- An `inventory.adjustment_rejected` audit log entry is written.

### Request body
`{"reason": "..."}`. **Required**, non-blank, up to 1000 characters. Shown to the requester as
`rejection_reason` on the adjustment.

### Permission & scope
- Requires `APPROVE_STOCK_ADJUSTMENT` (same as approve).
- The request must belong to this organization and one of the caller's locations; otherwise 404.
- Approve and reject racing on the same request is safe: the row is locked and the second call gets 400.""",
    request=StockAdjustmentRejectSerializer,
    examples=[
        OpenApiExample(
            "Reject",
            value={"reason": "Recount found the 5 units on the back shelf."},
            request_only=True,
        )
    ],
    responses={
        200: OpenApiResponse(
            response=StockActionResultSerializer,
            description="Rejected. Stock unchanged.",
            examples=[OpenApiExample("Rejected", value={"status": "rejected"}, response_only=True)],
        ),
        400: OpenApiResponse(
            response=StockActionErrorSerializer,
            description=(
                "`reason` missing or blank (`{\"code\": \"VALIDATION_ERROR\", \"detail\": \"Invalid input.\", "
                "\"errors\": {\"reason\": [\"...\"]}}`), or the request is no longer PENDING (`APPROVAL_ERROR`)."
            ),
            examples=[
                OpenApiExample(
                    "Not pending",
                    value={"code": "APPROVAL_ERROR", "detail": "Only PENDING requests can be rejected."},
                    response_only=True,
                )
            ],
        ),
        403: OpenApiResponse(description="The caller lacks `APPROVE_STOCK_ADJUSTMENT`."),
        404: OpenApiResponse(
            description="No such request in this organization, or it is at a location the caller can't access."
        ),
    },
)

stock_adjustment_schema = extend_schema_view(
    create=extend_schema(
        tags=['Inventory'],
        summary="Request Stock Adjustment",
        description="""Submits a stock adjustment request for inventory discrepancies.

### Permission Requirements (PRD §28):
- **Submission**: Requires `REQUEST_STOCK_ADJUSTMENT` permission.
- **Auto-Approval**: Users with `APPROVE_STOCK_ADJUSTMENT`, `APPROVE_REQUESTS`, or system Owner/Administrator roles auto-approve in the same request. Standard users create `PENDING` requests.

### Plan Limit & Subscription Standing (PRD §10):
- **Active Subscription**: Validates `ensure_subscription_active(product.organization)`.
- **Monthly Transactions**: Approved adjustments generate an immutable system transaction governed by the `max_transactions_per_month` plan limit.
- **Locations Quota**: Target warehouse must be active under `max_locations`.

### Tenant Isolation (PRD §6):
- The `product`, `warehouse`, and user must all belong to the same organization. Cross-tenant references return HTTP 400 Bad Request.
- `warehouse` must be one of the caller's locations: `403 LOCATION_ACCESS_DENIED` otherwise.""",
        examples=[stock_adjustment_create_example]
    ),
    list=extend_schema(
        tags=['Inventory'],
        summary="List Stock Adjustment Requests",
        description="""Retrieves a list of stock adjustment requests for the user's organization.

### Permission Requirements:
- Requires `VIEW_INVENTORY` or `VIEW_APPROVALS`.

### Scoping:
- Scoped to the organization in the URL and to the caller's locations.

""" + LOCATION_ACCESS_NOTE,
        parameters=[LOCATION_FILTER_PARAM, PRODUCT_FILTER_PARAM]
    ),
    retrieve=extend_schema(
        tags=['Inventory'],
        summary="Retrieve Stock Adjustment Request",
        description="""Fetch a specific stock adjustment request by ID.

### Permission & Security:
- Requires `VIEW_INVENTORY`.
- Cross-tenant requests return HTTP 404 Not Found.""",
    ),
    approve=stock_adjustment_approve_schema,
    reject=stock_adjustment_reject_schema,
)

stock_transfer_schema = extend_schema_view(
    execute=extend_schema(
        tags=['Inventory'],
        summary="Transfer Stock Between Warehouses",
        description="""Atomically moves stock from a source warehouse to a destination warehouse.

### Workflow & Invariants (PRD §15, §16):
- Deducts stock from source warehouse `Inventory` and increments destination warehouse `Inventory`.
- Writes dual immutable `InventoryLedgerEntry` records (`-quantity` and `+quantity`).
- Deductions fail with HTTP 400 Bad Request if available source stock is insufficient.

### Permission Requirements (PRD §28):
- Requires `TRANSFER_STOCK` permission.
- Both warehouses must belong to the caller's active organization.
- The caller must have access to **both** locations: `403 LOCATION_ACCESS_DENIED` otherwise.

### Plan Limits & Subscription Standing (PRD §10, §52):
- Enforces active subscription standing (`ensure_subscription_active`).
- Counts as a system transaction against `max_transactions_per_month`.
- Both facilities are subject to the `max_locations` plan quota.""",
        request=StockTransferSerializer,
        examples=[stock_transfer_example],
        responses={
            200: OpenApiResponse(
                response=StockActionResultSerializer,
                description="Transfer done: source decreased, destination increased, two ledger entries written.",
                examples=[
                    OpenApiExample("Transferred", value={"status": "transfer completed"}, response_only=True)
                ],
            ),
            400: OpenApiResponse(
                response=StockActionErrorSerializer,
                description=(
                    "One of:\n"
                    "- `TRANSFER_ERROR`: not enough stock at the source, or source and destination are the same.\n"
                    "- `VALIDATION_ERROR` with \"Product, variant, batch or warehouse not found in this "
                    "organization.\": an id doesn't exist in this organization.\n"
                    "- `VALIDATION_ERROR` (`errors` keyed by field) for a malformed body, e.g. `quantity` < 1."
                ),
                examples=[
                    OpenApiExample(
                        "Insufficient stock",
                        value={
                            "code": "TRANSFER_ERROR",
                            "detail": "Out of stock: Coca-Cola 50cl. Requested 500, available 30.",
                        },
                        response_only=True,
                    )
                ],
            ),
            403: OpenApiResponse(
                description=(
                    "The caller lacks `TRANSFER_STOCK`, or `LOCATION_ACCESS_DENIED`: one of the two "
                    "locations isn't one of theirs."
                )
            ),
        }
    )
)

inventory_schema = extend_schema_view(
    list=extend_schema(
        tags=['Inventory'],
        summary="List Inventory Balances",
        description="""Retrieves current real-time stock balances across products, variants, batches, and warehouses.

### Permission Requirements (PRD §28, §55):
- Requires `VIEW_INVENTORY` permission.
- For cost details on related products, `purchase_cost` and batch `cost_price` are dynamically masked if the caller lacks `VIEW_PRODUCT_COSTS`.

### Stock per location (PRD §35):
- One row per product / variant / batch **per location**. To show "stock at this branch",
  call with `?location=<warehouse id>`; add `?product=<id>` for one product's stock at
  each of the caller's locations.
- For a product list with stock already summed, use `GET /products/?location=` instead
  (one request, no client-side summing).

### Plan Limits & Tracking Invariants (PRD §10, §13):
- Stock is strictly enforced for products with `track_inventory=True` (bounded by `max_products`).
- Untracked items (services, digital goods) can sell without stock limits.

""" + LOCATION_ACCESS_NOTE,
        parameters=[LOCATION_FILTER_PARAM, PRODUCT_FILTER_PARAM]
    ),
    retrieve=extend_schema(
        tags=['Inventory'],
        summary="Retrieve Specific Inventory Balance",
        description="""Fetch a specific inventory balance record by ID.

### Permission & Tenant Isolation:
- Requires `VIEW_INVENTORY`.
- Requesting an inventory record of another organization returns HTTP 404 Not Found.""",
    ),
    summary=extend_schema(
        tags=['Inventory'],
        summary="Get Inventory Stock Valuation & Attention Summary",
        description="""Computes the 4 KPI summary cards for the Products and Inventory dashboard:
- **`stock_value`**: Total valuation of on-hand inventory at purchase cost (`Σ (quantity × purchase_cost)`).
- **`potential_sales_value`**: Total valuation of on-hand inventory at retail selling price (`Σ (quantity × customer_sale_price)`).
- **`profit_to_be_made`**: Projected gross profit (`potential_sales_value - stock_value`).
- **`stock_requiring_attention`**: Count of active tracked products that are low stock (`current_stock <= minimum_stock_level`) or out of stock (`current_stock == 0`).

### Warehouse Scoping:
- Omit `warehouse` to retrieve organization-wide metrics across all active branches.
- Pass `?warehouse=<uuid>` to scope all valuation and attention figures strictly to that warehouse.

### Cost Security (PRD §12, §27, §55):
- Requires `VIEW_INVENTORY`.
- If the caller lacks `VIEW_PRODUCT_COSTS`, `stock_value` and `profit_to_be_made` are returned as `null` (`has_cost_permission: false`).""",
        parameters=[
            OpenApiParameter(
                "warehouse",
                OpenApiTypes.UUID,
                OpenApiParameter.QUERY,
                description="Optional warehouse UUID to scope summary metrics to a single branch.",
            ),
        ],
        responses={
            200: OpenApiResponse(
                response=InventorySummarySerializer,
                description="Inventory valuation and stock attention summary.",
                examples=[
                    OpenApiExample(
                        "Organization-Wide Inventory Summary",
                        value={
                            "stock_value": "1250000.00",
                            "potential_sales_value": "1850000.00",
                            "profit_to_be_made": "600000.00",
                            "stock_requiring_attention": 4,
                            "out_of_stock_count": 1,
                            "low_stock_count": 3,
                            "total_products": 45,
                            "total_inventory_items": 1420,
                            "warehouses_count": 2,
                            "warehouse_id": None,
                            "warehouse_name": "All Warehouses",
                            "has_cost_permission": True,
                        },
                        response_only=True,
                    )
                ],
            )
        },
    ),
)

ledger_entry_schema = extend_schema_view(
    list=extend_schema(
        tags=['Inventory'],
        summary="List Inventory Ledger Entries (Audit Trail)",
        description="""Retrieves the immutable, append-only audit trail of all inventory movements.

### Immutable Ledger Principles (PRD §8, §15, §44):
- Source of truth for all physical stock movements (Sales, Purchases, Returns, Adjustments, Transfers, Opening Stock).
- Entries are **read-only**: ledger records cannot be directly created, edited, or deleted via any API endpoint.

### Permission Requirements (PRD §28):
- Requires `VIEW_INVENTORY` or `VIEW_INVENTORY_REPORTS`.

""" + LOCATION_ACCESS_NOTE,
        parameters=[LOCATION_FILTER_PARAM, PRODUCT_FILTER_PARAM]
    ),
    retrieve=extend_schema(
        tags=['Inventory'],
        summary="Retrieve Specific Ledger Entry",
        description="""Fetch a specific ledger entry by ID.

### Permission & Security:
- Requires `VIEW_INVENTORY`.
- Cross-tenant requests return HTTP 404 Not Found.""",
    ),
)
