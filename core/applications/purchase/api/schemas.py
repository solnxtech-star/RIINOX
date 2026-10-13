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

from core.applications.purchase.api.serializers import (
    PurchaseCreateSerializer,
    PurchaseReceiveResponseSerializer,
    PurchaseReceiveSerializer,
    PurchaseSerializer,
)

# Reusable error and status building blocks

DetailErrorSerializer = inline_serializer(
    name="PurchaseDetailError",
    fields={"detail": serializers.CharField(help_text="Human-readable error description.")},
)

CodeDetailErrorSerializer = inline_serializer(
    name="PurchaseCodeDetailError",
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
    description="The caller lacks permission to perform this action (e.g. lacking VIEW_PURCHASES, CREATE_PURCHASE, or RECEIVE_GOODS).",
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
    description="The organization's subscription is inactive or has exceeded plan limits (e.g. max_transactions_per_month).",
    examples=[
        OpenApiExample(
            "Monthly Transaction Limit Reached",
            value={
                "code": "PLAN_LIMIT_EXCEEDED",
                "detail": "Plan limit reached for 'max_transactions_per_month'. Upgrade your subscription to continue processing goods receipts.",
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
    description="The requested purchase order was not found, or belongs to an organization the caller is not a member of.",
    examples=[
        OpenApiExample(
            "NotFound",
            value={"detail": "No Purchase matches the given query."},
            response_only=True,
        )
    ],
)


# Request and response examples

_purchase_create_example = OpenApiExample(
    "Standard Purchase Order with Line Items",
    summary="Create PO with Product UUIDs and line items",
    description=(
        "Full purchase order creation payload including header info, vendor, destination warehouse, "
        "and line items. Each line item requires the catalog Product UUID (obtained from GET /api/products/products/). "
        "Snapshots purchase costs at order time. Does NOT update inventory."
    ),
    value={
        "organization": "a1b2c3d4-e5f6-4789-b1c2-d3e4f5a6b7c8",
        "vendor": "f1e2d3c4-b5a6-4789-b1c2-d3e4f5a6b7c8",
        "warehouse": "c1d2e3f4-a5b6-4789-b1c2-d3e4f5a6b7c8",
        "vendor_invoice_number": "INV-VEN-2026-9812",
        "delivery_method": "Supplier Delivery",
        "other_costs": "5000.00",
        "discount": "1000.00",
        "order_date": "2026-10-09",
        "delivery_date": "2026-10-16",
        "notes": "Restocking core warehouse items for Q4 wholesale distribution.",
        "items": [
            {
                "product": "d1e2f3a4-b5c6-4789-b1c2-d3e4f5a6b7c8",
                "variant": "e2f3a4b5-c6d7-4789-b1c2-d3e4f5a6b7c8",
                "unit": "b3c4d5e6-f7a8-4789-b1c2-d3e4f5a6b7c8",
                "quantity_ordered": 100,
                "purchase_cost": "2500.00",
                "discount": "500.00",
            },
            {
                "product": "c4d5e6f7-a8b9-4789-b1c2-d3e4f5a6b7c8",
                "quantity_ordered": 50,
                "purchase_cost": "1200.00",
            },
        ],
    },
    request_only=True,
)

_purchase_response_example = OpenApiExample(
    "Purchase Order Response",
    summary="Complete PO representation",
    description="Full representation of a purchase order with calculated totals, order status, and item lines.",
    value={
        "id": "7b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
        "purchase_number": "PO-2026-0004",
        "organization": "a1b2c3d4-e5f6-4789-b1c2-d3e4f5a6b7c8",
        "vendor": "f1e2d3c4-b5a6-4789-b1c2-d3e4f5a6b7c8",
        "warehouse": "c1d2e3f4-a5b6-4789-b1c2-d3e4f5a6b7c8",
        "ordered_by": "11111111-2222-3333-4444-555555555555",
        "received_by": None,
        "vendor_invoice_number": "INV-VEN-2026-9812",
        "supporting_document": None,
        "delivery_date": "2026-10-16",
        "order_date": "2026-10-09",
        "delivery_method": "Supplier Delivery",
        "other_costs": "5000.00",
        "discount": "1000.00",
        "status": "draft",
        "notes": "Restocking core warehouse items for Q4 wholesale distribution.",
        "items": [
            {
                "id": "8c2deb4d-4c8d-5cae-acde-3c1e8c4dcb7e",
                "purchase": "7b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
                "product": "d1e2f3a4-b5c6-4789-b1c2-d3e4f5a6b7c8",
                "variant": "e2f3a4b5-c6d7-4789-b1c2-d3e4f5a6b7c8",
                "unit": "b3c4d5e6-f7a8-4789-b1c2-d3e4f5a6b7c8",
                "quantity_ordered": 100,
                "quantity_received": 0,
                "purchase_cost": "2500.00",
                "discount": "500.00",
                "received_value": "0.00",
                "ordered_value": "249500.00",
                "created_at": "2026-10-09T14:00:00Z",
            }
        ],
        "total_value": "4000.00",
        "ordered_value": "253500.00",
        "created_at": "2026-10-09T14:00:00Z",
    },
    response_only=True,
)

_purchase_receive_request_example = OpenApiExample(
    "Partial or Full Goods Receipt",
    summary="Receive items payload",
    description=(
        "Records physical arrival of goods for one or more purchase line items. "
        "Every received item immediately generates an InventoryLedgerEntry and increases warehouse stock."
    ),
    value={
        "items_received": [
            {
                "item_id": "8c2deb4d-4c8d-5cae-acde-3c1e8c4dcb7e",
                "quantity_received": 50,
            }
        ]
    },
    request_only=True,
)

_purchase_receive_response_example = OpenApiExample(
    "Goods Receipt Success",
    summary="Receipt confirmation",
    description="Result of goods receipt, including updated lifecycle status and count of lines processed.",
    value={
        "status": "received",
        "purchase_status": "partially_received",
        "items_received_count": 1,
    },
    response_only=True,
)

_purchase_update_items_example = OpenApiExample(
    "Update Line Items (Product Revision)",
    summary="Update products, quantities, and costs on draft PO",
    description=(
        "Replaces or updates ordered products, quantities, and negotiated costs on a draft purchase order "
        "before any goods have arrived. Permitted strictly while quantity_received == 0."
    ),
    value={
        "notes": "Updated line items: revised product quantities and costs based on updated vendor quotation.",
        "other_costs": "4500.00",
        "discount": "800.00",
        "items": [
            {
                "product": "d1e2f3a4-b5c6-4789-b1c2-d3e4f5a6b7c8",
                "variant": "e2f3a4b5-c6d7-4789-b1c2-d3e4f5a6b7c8",
                "unit": "b3c4d5e6-f7a8-4789-b1c2-d3e4f5a6b7c8",
                "quantity_ordered": 150,
                "purchase_cost": "2400.00",
                "discount": "200.00",
            },
            {
                "product": "c4d5e6f7-a8b9-4789-b1c2-d3e4f5a6b7c8",
                "quantity_ordered": 75,
                "purchase_cost": "1150.00",
                "discount": "50.00",
            },
        ],
    },
    request_only=True,
)

_purchase_partial_update_metadata_example = OpenApiExample(
    "Update Header Metadata",
    summary="Update invoice reference and delivery date",
    description="Updates non-item metadata fields such as vendor invoice number, delivery date, or operational notes.",
    value={
        "vendor_invoice_number": "INV-VEN-2026-9901",
        "delivery_date": "2026-10-20",
        "delivery_method": "Express Freight Delivery",
        "notes": "Supplier confirmed dispatch via courier; updated invoice number and delivery date.",
    },
    request_only=True,
)



# Purchase ViewSet schema contract

purchase_schema = extend_schema_view(
    list=extend_schema(
        tags=["Purchases"],
        summary="List Purchase Orders",
        description=(
            "### Overview\n"
            "Retrieves a paginated list of purchase orders for organizations the authenticated user belongs to. "
            "Implements strict multi-tenant boundary isolation (PRD §6).\n\n"
            "### Required Permissions & Access Control (PRD §28, §55)\n"
            "- Requires **`VIEW_PURCHASES`** permission in the target tenant organization.\n"
            "- Unauthenticated requests return HTTP 401.\n"
            "- Authenticated callers without active membership or `VIEW_PURCHASES` capability receive HTTP 403 Forbidden.\n\n"
            "### Plan Limits & Subscription Invariants (PRD §10, §52)\n"
            "- Read operations require active tenant organization standing.\n"
            "- Querying purchase orders is read-only and does NOT consume transaction quota allocations (`max_transactions_per_month`).\n\n"
            "### Business Context (PRD §18)\n"
            "A Purchase Order tracks goods ordered from an external vendor. The header summarizes procurement metadata "
            "such as vendor, target warehouse, order dates, delivery methods, additional fees (shipping/handling), "
            "and vendor invoice references.\n\n"
            "### Financial Calculations\n"
            "- **`ordered_value`**: Total monetary value of items as originally ordered, net of discounts plus other costs.\n"
            "- **`total_value`**: Total monetary value of items **physically received so far**, net of prorated discounts "
            "plus other costs. Only received value reflects actual supplier liability.\n\n"
            "### Query Parameters\n"
            "- `organization_id`: (UUID in URL path) Target tenant organization."
        ),
        parameters=[
            OpenApiParameter(
                name="organization_id",
                type=OpenApiTypes.UUID,
                location=OpenApiParameter.PATH,
                description="UUID of the tenant organization owning the purchase orders.",
            ),
        ],
        responses={
            200: OpenApiResponse(
                response=PurchaseSerializer(many=True),
                description="Paginated list of purchase orders.",
            ),
            401: UNAUTHENTICATED_RESPONSE,
            403: FORBIDDEN_RESPONSE,
        },
    ),
    retrieve=extend_schema(
        tags=["Purchases"],
        summary="Retrieve a Purchase Order",
        description=(
            "### Overview\n"
            "Fetches detailed information for a single purchase order by UUID, including all nested line items, "
            "fulfillment progress, and financial value metrics.\n\n"
            "### Required Permissions & Access Control (PRD §28, §55)\n"
            "- Requires **`VIEW_PURCHASES`** permission in the target tenant organization.\n"
            "- Cross-tenant lookups return HTTP 404 to prevent entity enumeration across organizational boundaries.\n\n"
            "### Line Item Details (PRD §18)\n"
            "Each line item details the ordered `quantity_ordered`, the fulfilled `quantity_received`, the unit of "
            "measurement (`unit`), and the cost per unit (`purchase_cost`).\n\n"
            "### Immutability & Audit Trail\n"
            "The `purchase_cost` on each line item is permanently snapshotted at order creation time. Subsequent price "
            "changes in the master product catalog do not rewrite historical procurement costs."
        ),
        responses={
            200: OpenApiResponse(
                response=PurchaseSerializer,
                description="Purchase order details retrieved successfully.",
                examples=[_purchase_response_example],
            ),
            401: UNAUTHENTICATED_RESPONSE,
            403: FORBIDDEN_RESPONSE,
            404: NOT_FOUND_RESPONSE,
        },
    ),
    create=extend_schema(
        tags=["Purchases"],
        summary="Create a Purchase Order",
        description=(
            "### Overview\n"
            "Creates a new purchase order and its associated line items in an atomic database transaction.\n\n"
            "### Required Permissions & Access Control (PRD §28, §55)\n"
            "- Requires **`CREATE_PURCHASE`** permission in the target organization.\n"
            "- Roles without this permission (e.g. Sales Representative, Inventory Staff) receive HTTP 403 Forbidden.\n\n"
            "### Plan Limits & Subscription Invariants (PRD §10, §52)\n"
            "- **Subscription Standing**: Evaluates `ensure_subscription_active(organization)`. If the subscription is inactive, delinquent, or suspended, creation is rejected with HTTP 402/403 (`SUBSCRIPTION_INACTIVE`).\n"
            "- **Location Quota**: The destination `warehouse` must belong to the tenant and be an active location within the organization's `max_locations` plan limit.\n"
            "- **Monthly Transactions Quota**: Placing an order in `draft` status does NOT increment monthly transaction counts (`max_transactions_per_month`) because physical inventory has not moved yet. Quotas are enforced when goods arrive via `/receive/`.\n\n"
            "### Critical Inventory Rule (PRD §16 & §18)\n"
            "**Creating a purchase order does NOT alter inventory stock.**\n"
            "- The order is initially placed in `draft` status.\n"
            "- Inventory quantities and ledger entries are untouched until physical goods are checked in via the "
            "`/receive/` endpoint.\n"
            "- Prevents phantom stock from appearing in warehouse availability before delivery.\n\n"
            "### Request Payload Structure & Example\n"
            "```json\n"
            "{\n"
            '  "organization": "a1b2c3d4-e5f6-4789-b1c2-d3e4f5a6b7c8",\n'
            '  "vendor": "f1e2d3c4-b5a6-4789-b1c2-d3e4f5a6b7c8",\n'
            '  "warehouse": "c1d2e3f4-a5b6-4789-b1c2-d3e4f5a6b7c8",\n'
            '  "delivery_method": "Supplier Delivery",\n'
            '  "other_costs": "5000.00",\n'
            '  "discount": "1000.00",\n'
            '  "order_date": "2026-10-09",\n'
            '  "delivery_date": "2026-10-16",\n'
            '  "notes": "Restocking core warehouse items for Q4 wholesale distribution.",\n'
            '  "items": [\n'
            '    {\n'
            '      "product": "d1e2f3a4-b5c6-4789-b1c2-d3e4f5a6b7c8",\n'
            '      "variant": "e2f3a4b5-c6d7-4789-b1c2-d3e4f5a6b7c8",\n'
            '      "unit": "b3c4d5e6-f7a8-4789-b1c2-d3e4f5a6b7c8",\n'
            '      "quantity_ordered": 100,\n'
            '      "purchase_cost": "2500.00",\n'
            '      "discount": "500.00"\n'
            '    }\n'
            '  ]\n'
            "}\n"
            "```\n\n"
            "### Field Roles\n"
            "- **`product` (UUID)**: Required. Primary key of catalog Product (from `GET /api/products/products/`). "
            "Does NOT need to already exist in warehouse inventory.\n"
            "- **`purchase_cost`**: Agreed unit procurement cost, snapshotted at creation.\n"
            "- **`quantity_ordered`**: Must be a positive integer (`>= 1`).\n\n"
            "### Edge Cases & Validation\n"
            "- **Active Subscription**: The organization's subscription must be active; suspended or cancelled accounts cannot initiate purchases.\n"
            "- **Vendor & Warehouse Tenant Check**: The specified `vendor` and `warehouse` must belong to the specified `organization`.\n"
            "- **Line Items**: Quantities must be positive integers (`quantity_ordered >= 1`). Unit purchase costs must be `>= 0.00`."
        ),
        request=PurchaseCreateSerializer,
        examples=[_purchase_create_example],
        responses={
            201: OpenApiResponse(
                response=PurchaseSerializer,
                description="Purchase order successfully created in draft status.",
                examples=[_purchase_response_example],
            ),
            400: OpenApiResponse(
                response=CodeDetailErrorSerializer,
                description="Validation error (e.g. missing required fields, invalid vendor, or negative quantities).",
                examples=[
                    OpenApiExample(
                        "Validation Error",
                        value={"vendor": ["This field is required."], "items": ["This field is required."]},
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
        tags=["Purchases"],
        summary="Update a Purchase Order",
        description=(
            "### Overview\n"
            "Replaces all editable fields of an existing purchase order and refreshes its line items.\n\n"
            "### Required Permissions & Access Control (PRD §28, §55)\n"
            "- Requires **`CREATE_PURCHASE`** permission in the organization.\n\n"
            "### Plan Limits & Subscription Invariants (PRD §10, §52)\n"
            "- Evaluates `ensure_subscription_active(organization)` to ensure account is in good standing.\n\n"
            "### PRD Data Integrity Constraint (PRD §18 & §44)\n"
            "**Line items cannot be modified once goods have been received.**\n"
            "- If any line item has `quantity_received > 0`, attempting to rewrite line items will be rejected with "
            "HTTP 400 (`Cannot modify line items on a purchase order that has already received goods`).\n"
            "- This guarantees that ledger entries created during goods receipt always link to immutable, historical line items.\n\n"
            "### Product & Line Item Revision\n"
            "To revise ordered products, quantities, or negotiated costs on a draft order before receiving, "
            "provide a complete updated `items` array with the target `product` UUIDs.\n\n"
            "### Lifecycle Transitions & Audit\n"
            "- Transitioning `status` to `ordered` emits an audit record (`PURCHASE_ORDER_APPROVED`).\n"
            "- Standard field edits emit an audit event (`PURCHASE_ORDER_UPDATED`) tracking previous and updated values."
        ),
        request=PurchaseCreateSerializer,
        examples=[
            _purchase_create_example,
            _purchase_update_items_example,
        ],
        responses={
            200: OpenApiResponse(
                response=PurchaseSerializer,
                description="Purchase order successfully updated.",
            ),
            400: OpenApiResponse(
                response=DetailErrorSerializer,
                description="Business rule violation (e.g. attempting to modify items on an order with received goods).",
                examples=[
                    OpenApiExample(
                        "Cannot Modify Received Items",
                        value={"detail": "Cannot modify line items on a purchase order that has already received goods."},
                        response_only=True,
                    )
                ],
            ),
            401: UNAUTHENTICATED_RESPONSE,
            402: PLAN_LIMIT_EXCEEDED_RESPONSE,
            403: FORBIDDEN_RESPONSE,
            404: NOT_FOUND_RESPONSE,
        },
    ),
    partial_update=extend_schema(
        tags=["Purchases"],
        summary="Partially Update a Purchase Order",
        description=(
            "### Overview\n"
            "Updates specific attributes of a purchase order without supplying the entire payload.\n\n"
            "### Required Permissions & Access Control (PRD §28, §55)\n"
            "- Requires **`CREATE_PURCHASE`** permission in the organization.\n\n"
            "### Plan Limits & Subscription Invariants (PRD §10, §52)\n"
            "- Evaluates `ensure_subscription_active(organization)`.\n\n"
            "### Common Use Cases\n"
            "- **Revising Products / Line Items**: Send an `items` array with updated `product` UUIDs, quantities, and costs (only when `quantity_received == 0`).\n"
            "- **Attaching Vendor Invoices**: Send `vendor_invoice_number` after invoice receipt.\n"
            "- **Uploading Documents**: Attach `supporting_document` (waybill, delivery note, or scanned invoice).\n"
            "- **Updating Logistics**: Update `notes`, `delivery_method`, or estimated `delivery_date`.\n\n"
            "### Product Revision Example (PATCH)\n"
            "```json\n"
            "{\n"
            '  "items": [\n'
            '    {\n'
            '      "product": "d1e2f3a4-b5c6-4789-b1c2-d3e4f5a6b7c8",\n'
            '      "quantity_ordered": 150,\n'
            '      "purchase_cost": "2400.00",\n'
            '      "discount": "200.00"\n'
            '    }\n'
            '  ]\n'
            "}\n"
            "```\n\n"
            "### Constraints\n"
            "- If `items` is included in the payload, the order must not have any received goods (`quantity_received == 0`)."
        ),
        request=PurchaseCreateSerializer,
        examples=[
            _purchase_update_items_example,
            _purchase_partial_update_metadata_example,
        ],
        responses={
            200: OpenApiResponse(
                response=PurchaseSerializer,
                description="Purchase order partially updated successfully.",
            ),
            400: OpenApiResponse(
                response=DetailErrorSerializer,
                description="Invalid field values or illegal modification of received lines.",
            ),
            401: UNAUTHENTICATED_RESPONSE,
            402: PLAN_LIMIT_EXCEEDED_RESPONSE,
            403: FORBIDDEN_RESPONSE,
            404: NOT_FOUND_RESPONSE,
        },
    ),
    destroy=extend_schema(
        tags=["Purchases"],
        summary="Delete a Purchase Order",
        description=(
            "### Overview\n"
            "Permanently deletes an unfulfilled purchase order record.\n\n"
            "### Required Permissions & Access Control (PRD §28, §55)\n"
            "- Requires **`CREATE_PURCHASE`** permission in the organization.\n\n"
            "### Strict Data Integrity & Audit Rule (PRD §18 & §44)\n"
            "**Orders that have received goods cannot be deleted.**\n"
            "- If any line item has `quantity_received > 0`, the deletion request is blocked and returns "
            "HTTP 400 (`CANNOT_DELETE_RECEIVED_PURCHASE`). The order must be cancelled instead.\n"
            "- Protecting fulfilled orders prevents silent inventory variances and preserves the audit chain: "
            "`Supplier > Purchase > Receiving > Inventory` (PRD §31).\n\n"
            "### Audit Event\n"
            "- Deleting an unreceived draft purchase order dispatches an immutable audit log (`purchase_order.deleted`)."
        ),
        responses={
            204: OpenApiResponse(description="Purchase order successfully deleted."),
            400: OpenApiResponse(
                response=CodeDetailErrorSerializer,
                description="Deletion rejected because goods were already checked into inventory.",
                examples=[
                    OpenApiExample(
                        "Cannot Delete Received Order",
                        value={
                            "code": "CANNOT_DELETE_RECEIVED_PURCHASE",
                            "detail": "Cannot delete a purchase order that has already received goods. Cancel it instead.",
                        },
                        response_only=True,
                    )
                ],
            ),
            401: UNAUTHENTICATED_RESPONSE,
            403: FORBIDDEN_RESPONSE,
            404: NOT_FOUND_RESPONSE,
        },
    ),
    receive=extend_schema(
        tags=["Purchases"],
        summary="Receive Goods (Stock Check-In)",
        description=(
            "### Overview\n"
            "Records the physical arrival and check-in of ordered goods at the target warehouse (PRD §16, §18, §42).\n\n"
            "### Required Permissions & Access Control (PRD §28, §55)\n"
            "- Requires **`RECEIVE_GOODS`** permission in the target tenant organization.\n"
            "- Non-receiving roles (e.g. Sales Representative or roles with only `VIEW_PURCHASES`) are rejected with HTTP 403 Forbidden.\n\n"
            "### Plan Limits & Subscription Invariants (PRD §10, §52)\n"
            "- **Active Subscription**: Calls `ensure_subscription_active(purchase.organization)`. Inactive or delinquent subscriptions immediately reject goods receipt with HTTP 402/403.\n"
            "- **Monthly Transactions Limit**: Receiving goods generates a centralized system `Transaction` (type `PURCHASE`, status `COMPLETED`), enforcing `check_plan_limit(organization, 'max_transactions_per_month', cycle_count)`. If the monthly limit is exceeded, receipt is blocked with HTTP 402/403 (`PLAN_LIMIT_EXCEEDED`).\n\n"
            "### Atomic Operations & Ledger Integration (PRD §16, §18, §31, §42)\n"
            "When this endpoint is called, the following steps execute inside a single `@transaction.atomic` block:\n"
            "1. **Subscription & Quota Validation**: Verifies active subscription standing (`ensure_subscription_active`) and monthly transaction quotas.\n"
            "2. **Line Item Validation**: Verifies that each `item_id` belongs to this order and that `quantity_received` is valid.\n"
            "3. **Transaction Creation**: Creates a centralized `Transaction` record (type `PURCHASE`, status `COMPLETED`).\n"
            "4. **Inventory Stock Increment**: Increases physical stock in `Inventory.quantity` for the product/variant in the destination warehouse.\n"
            "5. **Ledger Recording**: Creates an immutable `InventoryLedgerEntry` documenting positive `quantity_moved`, snapshotted `unit_cost`, "
            "`warehouse`, and reference transaction.\n"
            "6. **PO Status Transition**:\n"
            "   - If all items have reached their ordered quantity -> Status becomes `received`.\n"
            "   - If partial quantities have been received -> Status becomes `partially_received`.\n"
            "7. **Audit Event**: Logs `AuditAction.PURCHASE_ORDER_RECEIVED` recording the receiver and quantity change.\n\n"
            "### Critical Edge Cases Handled (PRD §18)\n"
            "- **Cancelled Orders**: Cannot receive goods against a `cancelled` purchase order (HTTP 400: `Cannot receive goods against a cancelled purchase order`).\n"
            "- **Over-Receiving Guard**: Cannot receive more units than `quantity_ordered - quantity_received` "
            "(HTTP 400: `Cannot receive X units. Already received Y of Z ordered`).\n"
            "- **Non-Positive Quantities**: `quantity_received` must be `>= 1` (HTTP 400: `Received quantity must be greater than zero`).\n"
            "- **Invalid Item UUID**: Each `item_id` must match a valid `PurchaseItem` belonging to this specific order.\n"
            "- **Receiver Accountability**: Records the authenticated user as `received_by` for strict operational attribution."
        ),
        request=PurchaseReceiveSerializer,
        examples=[_purchase_receive_request_example],
        responses={
            200: OpenApiResponse(
                response=PurchaseReceiveResponseSerializer,
                description="Goods successfully checked in, stock updated, and ledger entry created.",
                examples=[_purchase_receive_response_example],
            ),
            400: OpenApiResponse(
                response=CodeDetailErrorSerializer,
                description="Goods receipt validation failure.",
                examples=[
                    OpenApiExample(
                        "Over-Receiving Rejected",
                        value={
                            "code": "RECEIVE_ERROR",
                            "detail": "Cannot receive 150 units of 'Widget A'. Already received 0 of 100 ordered.",
                        },
                        response_only=True,
                    ),
                    OpenApiExample(
                        "Cancelled Order Rejected",
                        value={
                            "code": "RECEIVE_ERROR",
                            "detail": "Cannot receive goods against a cancelled purchase order.",
                        },
                        response_only=True,
                    ),
                    OpenApiExample(
                        "Non-Positive Quantity Rejected",
                        value={
                            "code": "RECEIVE_ERROR",
                            "detail": "Received quantity must be greater than zero for 'Widget A'.",
                        },
                        response_only=True,
                    ),
                ],
            ),
            401: UNAUTHENTICATED_RESPONSE,
            402: PLAN_LIMIT_EXCEEDED_RESPONSE,
            403: FORBIDDEN_RESPONSE,
            404: NOT_FOUND_RESPONSE,
        },
    ),
)
