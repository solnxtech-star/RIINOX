from drf_spectacular.utils import (
    extend_schema,
    extend_schema_view,
    OpenApiExample,
    OpenApiParameter,
    OpenApiTypes,
    OpenApiResponse,
)
from core.applications.inventory.api.serializers import StockTransferSerializer

# ---------------------------------------------------------------------------
# Query & Header Parameters for Tenant Scoping
# ---------------------------------------------------------------------------
ORGANIZATION_QUERY_PARAM = OpenApiParameter(
    "organization",
    OpenApiTypes.UUID,
    OpenApiParameter.QUERY,
    description="Explicitly filter inventory records by organization UUID. By default, queries scope to the authenticated user's active organization memberships.",
)

ORGANIZATION_HEADER_PARAM = OpenApiParameter(
    "X-Organization-ID",
    OpenApiTypes.UUID,
    OpenApiParameter.HEADER,
    description="Header to explicitly scope requests to a specific organization UUID.",
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
- **Monthly Transactions Limit**: When approved, records a system `Transaction` (`transaction_type = 'ADJUSTMENT'`) evaluated against `check_plan_limit(organization, 'max_transactions_per_month')`.
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

### Plan Limits (PRD §10):
- Records a system `Transaction` (`transaction_type = 'ADJUSTMENT'`) bounded by the `max_transactions_per_month` plan quota.""",
    responses={
        200: OpenApiResponse(description="Adjustment Approved and Ledger Written"),
        400: OpenApiResponse(description="Approval Error (e.g. Request is not in PENDING status)"),
        403: OpenApiResponse(description="Forbidden (Insufficient approval privileges)"),
        404: OpenApiResponse(description="Adjustment request not found"),
    }
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
- The `product`, `warehouse`, and user must all belong to the same organization. Cross-tenant references return HTTP 400 Bad Request.""",
        examples=[stock_adjustment_create_example]
    ),
    list=extend_schema(
        tags=['Inventory'],
        summary="List Stock Adjustment Requests",
        description="""Retrieves a list of stock adjustment requests for the user's organization.

### Permission Requirements:
- Requires `VIEW_INVENTORY` or `VIEW_APPROVALS`.

### Tenant Scoping:
- Scoped strictly to the caller's active organization memberships. Filterable by `?organization=<UUID>` or `X-Organization-ID` header.""",
        parameters=[ORGANIZATION_QUERY_PARAM, ORGANIZATION_HEADER_PARAM]
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

### Plan Limits & Subscription Standing (PRD §10, §52):
- Enforces active subscription standing (`ensure_subscription_active`).
- Counts as a system transaction against `max_transactions_per_month`.
- Both facilities are subject to the `max_locations` plan quota.""",
        request=StockTransferSerializer,
        examples=[stock_transfer_example],
        responses={
            200: OpenApiResponse(description="Stock transfer successfully executed and recorded in ledger"),
            400: OpenApiResponse(description="Transfer Error (Insufficient stock or invalid warehouse IDs)"),
            403: OpenApiResponse(description="Forbidden (Insufficient transfer privileges)"),
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

### Multi-Tenant Scoping (PRD §6):
- Automatically filtered to the caller's active organizations via `Inventory.objects.for_user(user)`.
- Accepts `?organization=<UUID>` query parameter and `X-Organization-ID` header.

### Plan Limits & Tracking Invariants (PRD §10, §13):
- Stock is strictly enforced for products with `track_inventory=True` (bounded by `max_products`).
- Untracked items (services, digital goods) can sell without stock limits.""",
        parameters=[ORGANIZATION_QUERY_PARAM, ORGANIZATION_HEADER_PARAM]
    ),
    retrieve=extend_schema(
        tags=['Inventory'],
        summary="Retrieve Specific Inventory Balance",
        description="""Fetch a specific inventory balance record by ID.

### Permission & Tenant Isolation:
- Requires `VIEW_INVENTORY`.
- Requesting an inventory record of another organization returns HTTP 404 Not Found.""",
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

### Multi-Tenant Scoping (PRD §6):
- Filtered to the caller's active organization memberships via `InventoryLedgerEntry.objects.for_user(user)`.
- Accepts `?organization=<UUID>` query parameter and `X-Organization-ID` header.""",
        parameters=[ORGANIZATION_QUERY_PARAM, ORGANIZATION_HEADER_PARAM]
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
