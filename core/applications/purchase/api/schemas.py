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
    PurchaseCancelSerializer,
    PurchaseCreateSerializer,
    PurchaseInvoiceCreateSerializer,
    PurchaseInvoiceSerializer,
    PurchasePaymentCreateSerializer,
    PurchasePaymentSerializer,
    PurchaseReceiveResponseSerializer,
    PurchaseReceiveSerializer,
    PurchaseReturnCreateSerializer,
    PurchaseReturnSerializer,
    PurchaseSerializer,
    PurchaseStockActivitySerializer,
    PurchaseSummarySerializer,
    PurchaseUpdateSerializer,
)

# Reusable Path & Query Parameters

PURCHASE_ORGANIZATION_PARAM = OpenApiParameter(
    name="organization_id",
    type=OpenApiTypes.UUID,
    location=OpenApiParameter.PATH,
    description="UUID of the tenant organization owning the purchase orders.",
)

PURCHASE_ID_PARAM = OpenApiParameter(
    name="id",
    type=OpenApiTypes.UUID,
    location=OpenApiParameter.PATH,
    description="UUID of the target Purchase Order.",
)

PURCHASE_LIST_FILTER_PARAMS = [
    PURCHASE_ORGANIZATION_PARAM,
    OpenApiParameter(
        name="status",
        type=OpenApiTypes.STR,
        location=OpenApiParameter.QUERY,
        required=False,
        description="Filter by purchase order lifecycle status (`draft`, `ordered`, `partially_received`, `received`, `cancelled`).",
    ),
    OpenApiParameter(
        name="payment_status",
        type=OpenApiTypes.STR,
        location=OpenApiParameter.QUERY,
        required=False,
        description="Filter by supplier payment status (`unpaid`, `partially_paid`, `paid`).",
    ),
    OpenApiParameter(
        name="vendor",
        type=OpenApiTypes.UUID,
        location=OpenApiParameter.QUERY,
        required=False,
        description="Filter purchase orders belonging to a specific supplier/vendor UUID.",
    ),
    OpenApiParameter(
        name="warehouse",
        type=OpenApiTypes.UUID,
        location=OpenApiParameter.QUERY,
        required=False,
        description="Filter purchase orders targeted at a specific destination warehouse UUID.",
    ),
    OpenApiParameter(
        name="order_date_after",
        type=OpenApiTypes.DATE,
        location=OpenApiParameter.QUERY,
        required=False,
        description="Filter purchase orders with `order_date` on or after this date (`YYYY-MM-DD`).",
    ),
    OpenApiParameter(
        name="order_date_before",
        type=OpenApiTypes.DATE,
        location=OpenApiParameter.QUERY,
        required=False,
        description="Filter purchase orders with `order_date` on or before this date (`YYYY-MM-DD`).",
    ),
    OpenApiParameter(
        name="delivery_date_after",
        type=OpenApiTypes.DATE,
        location=OpenApiParameter.QUERY,
        required=False,
        description="Filter purchase orders with `delivery_date` on or after this date (`YYYY-MM-DD`).",
    ),
    OpenApiParameter(
        name="delivery_date_before",
        type=OpenApiTypes.DATE,
        location=OpenApiParameter.QUERY,
        required=False,
        description="Filter purchase orders with `delivery_date` on or before this date (`YYYY-MM-DD`).",
    ),
    OpenApiParameter(
        name="search",
        type=OpenApiTypes.STR,
        location=OpenApiParameter.QUERY,
        required=False,
        description="Case-insensitive search across `purchase_number`, `vendor__company_name`, `vendor_invoice_number`, and `notes`.",
    ),
    OpenApiParameter(
        name="ordering",
        type=OpenApiTypes.STR,
        location=OpenApiParameter.QUERY,
        required=False,
        description=(
            "Sort results by `created_at`, `order_date`, `delivery_date`, `purchase_number`, "
            "`status`, `payment_status`, or `amount_paid` (prefix with `-` for descending)."
        ),
    ),
]

# Reusable Error Serializers & Status Responses

DetailErrorSerializer = inline_serializer(
    name="PurchaseDetailError",
    fields={"detail": serializers.CharField(help_text="Human-readable error description.")},
)

CodeDetailErrorSerializer = inline_serializer(
    name="PurchaseCodeDetailError",
    fields={
        "code": serializers.CharField(help_text="Machine-readable error identifier."),
        "detail": serializers.CharField(
            help_text="Detailed explanation of the validation or business rule failure."
        ),
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
    description=(
        "The caller lacks permission to perform this action in the target organization "
        "(e.g. lacking `VIEW_PURCHASES`, `CREATE_PURCHASE`, `RECEIVE_GOODS`, `VIEW_PAYMENTS`, or `RECORD_PAYMENT`)."
    ),
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
    description=(
        "The organization's subscription is inactive or has reached plan limits "
        "(e.g. `max_transactions_per_month`)."
    ),
    examples=[
        OpenApiExample(
            "Monthly Transaction Limit Reached",
            value={
                "code": "PLAN_LIMIT_EXCEEDED",
                "detail": (
                    "Plan limit reached for 'max_transactions_per_month'. "
                    "Upgrade your subscription to continue processing goods receipts or payments."
                ),
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

# Request & Response Examples with Valid UUIDs

_purchase_create_minimal_example = OpenApiExample(
    "Minimal Purchase Order (Auto-Resolved Product Cost)",
    summary="Create PO with only required fields (auto-snapshots catalog cost)",
    description=(
        "Minimal payload for creating a Purchase Order. Only `vendor`, `warehouse`, and `items` (`product` + `quantity_ordered`) "
        "are needed. When `items[].purchase_cost` is omitted, the backend automatically snapshots `variant.purchase_cost` "
        "or `product.purchase_cost` (multiplied by `unit.multiplier` if a packaging unit is specified). "
        "`discount`, `other_costs`, and `items[].discount` default to `0.00` and can be supplied later when recording "
        "the Supplier Invoice (`POST /purchases/{id}/purchase-invoice/`)."
    ),
    value={
        "vendor": "1fd7d5fb-eb86-45be-a42e-d5cfd8396004",
        "warehouse": "d1328862-6a31-421b-94ac-dc25744f21fc",
        "order_date": "2026-10-28",
        "delivery_date": "2026-10-30",
        "items": [
            {
                "product": "1e5339e4-f1f9-4259-b849-70cf2c242e4c",
                "quantity_ordered": 10,
            },
        ],
    },
    request_only=True,
)

_purchase_create_example = OpenApiExample(
    "Standard Purchase Order with Negotiated Cost",
    summary="Create PO with explicit unit cost, dates, and delivery metadata",
    description=(
        "Full purchase order creation payload including header info, supplier/vendor, destination warehouse, "
        "and line items with an explicit negotiated `purchase_cost`. Each line item requires the catalog Product UUID "
        "(obtained from `GET /api/products/products/`). Does NOT update inventory until `/receive/` is called."
    ),
    value={
        "vendor": "1fd7d5fb-eb86-45be-a42e-d5cfd8396004",
        "warehouse": "d1328862-6a31-421b-94ac-dc25744f21fc",
        "vendor_invoice_number": "INV-VEN-2026-9812",
        "delivery_method": "Supplier Delivery",
        "order_date": "2026-10-28",
        "delivery_date": "2026-10-30",
        "notes": "Restocking core warehouse items for Q4 wholesale distribution.",
        "items": [
            {
                "product": "1e5339e4-f1f9-4259-b849-70cf2c242e4c",
                "quantity_ordered": 200,
                "purchase_cost": "2500.00",
            },
        ],
    },
    request_only=True,
)

_purchase_response_example = OpenApiExample(
    "Purchase Order Response",
    summary="Complete PO representation",
    description=(
        "Full representation of a purchase order with calculated totals, order status, "
        "payment status, contextual `available_actions`, and item lines."
    ),
    value={
        "id": "7b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
        "purchase_number": "PO-2026-0004",
        "organization": "a1b2c3d4-e5f6-4789-b1c2-d3e4f5a6b7c8",
        "vendor": "1fd7d5fb-eb86-45be-a42e-d5cfd8396004",
        "vendor_name": "TechSupply Global Ltd",
        "warehouse": "d1328862-6a31-421b-94ac-dc25744f21fc",
        "warehouse_name": "Main Warehouse - Lagos",
        "ordered_by": "11111111-2222-3333-4444-555555555555",
        "ordered_by_name": "Admin User",
        "received_by": None,
        "received_by_name": None,
        "cancelled_by": None,
        "cancelled_by_name": None,
        "cancelled_at": None,
        "cancel_reason": None,
        "vendor_invoice_number": "INV-VEN-2026-9812",
        "supporting_document": None,
        "delivery_date": "2026-10-16",
        "order_date": "2026-10-09",
        "delivery_method": "Supplier Delivery",
        "other_costs": "5000.00",
        "discount": "1000.00",
        "status": "draft",
        "payment_status": "unpaid",
        "amount_paid": "0.00",
        "subtotal": "249500.00",
        "total_value": "4000.00",
        "ordered_value": "253500.00",
        "balance_due": "253500.00",
        "available_actions": [
            "view",
            "edit",
            "cancel",
            "delete",
            "receive_goods",
            "record_purchase_invoice",
        ],
        "notes": "Restocking core warehouse items for Q4 wholesale distribution.",
        "items": [
            {
                "id": "8c2deb4d-4c8d-5cae-acde-3c1e8c4dcb7e",
                "purchase": "7b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
                "product": "1e5339e4-f1f9-4259-b849-70cf2c242e4c",
                "product_name": "Wireless Mouse",
                "product_sku": "WM-001",
                "variant": "e2f3a4b5-c6d7-4789-b1c2-d3e4f5a6b7c8",
                "variant_name": "Black",
                "unit": "b3c4d5e6-f7a8-4789-b1c2-d3e4f5a6b7c8",
                "unit_name": "carton",
                "unit_multiplier": "12.000",
                "quantity_ordered": 100,
                "quantity_received": 0,
                "purchase_cost": "2500.00",
                "discount": "500.00",
                "received_value": "0.00",
                "ordered_value": "249500.00",
                "created_at": "2026-10-09T14:00:00Z",
            }
        ],
        "created_at": "2026-10-09T14:00:00Z",
        "updated_at": "2026-10-09T14:00:00Z",
    },
    response_only=True,
)

_purchase_receive_request_example = OpenApiExample(
    "Partial or Full Goods Receipt",
    summary="Receive items payload with optional Batch No. and Expiry Date",
    description=(
        "Records physical arrival of goods for one or more purchase line items (UI Page 19). "
        "Every received item immediately resolves or creates a `ProductBatch` (when `batch_number` or `batch` is provided), "
        "applies packaging unit multipliers, generates an `InventoryLedgerEntry`, posts double-entry `FinancialLedgerEntry` "
        "rows (`Debit: INVENTORY_ASSET`, `Credit: ACCOUNTS_PAYABLE`), and increases warehouse stock."
    ),
    value={
        "items_received": [
            {
                "item_id": "8c2deb4d-4c8d-5cae-acde-3c1e8c4dcb7e",
                "quantity_received": 50,
                "batch_number": "BATCH-2026-10A",
                "expiry_date": "2027-10-31",
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
        "before any goods have arrived. Permitted strictly while `quantity_received == 0` across all lines."
    ),
    value={
        "notes": "Updated line items: revised product quantities and costs based on updated vendor quotation.",
        "other_costs": "4500.00",
        "discount": "800.00",
        "items": [
            {
                "product": "1e5339e4-f1f9-4259-b849-70cf2c242e4c",
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

_purchase_summary_response_example = OpenApiExample(
    "Purchase Order KPI Summary",
    summary="KPI card counters and financial totals",
    description="Aggregated counts and monetary totals powering the Purchase Order List header cards (UI Pages 4-6).",
    value={
        "total_purchase_orders": 24,
        "pending_delivery_count": 6,
        "partially_received_count": 3,
        "received_count": 13,
        "cancelled_count": 2,
        "unpaid_orders_count": 5,
        "partially_paid_orders_count": 4,
        "paid_orders_count": 13,
        "total_spend": "4850000.00",
        "total_ordered_value": "5620000.00",
        "total_paid": "3920000.00",
        "total_payable_balance": "930000.00",
    },
    response_only=True,
)

_purchase_cancel_request_example = OpenApiExample(
    "Cancel Purchase Order Request",
    summary="Cancel an unreceived draft or ordered PO",
    description="Supplies an optional cancellation reason recorded in the purchase order and audit trail (UI Page 14).",
    value={
        "reason": "Supplier unable to fulfill order within required delivery window.",
    },
    request_only=True,
)

_purchase_invoice_create_example = OpenApiExample(
    "Record Supplier Invoice (3-Step Wizard)",
    summary="Create or update Supplier Invoice with custom billed quantities and tax",
    description=(
        "Payload from the 3-Step 'Create New Invoice (Supplier Invoice)' wizard (UI Pages 1-3). "
        "If `items` is omitted, lines default to received (or ordered) quantities and snapshotted PO costs."
    ),
    value={
        "vendor_invoice_number": "SUP-INV-2026-0881",
        "billed_to": "Funke Adeleke - RIINOX Operations",
        "invoice_type": "supplier_invoice",
        "issue_date": "2026-10-15",
        "due_date": "2026-11-15",
        "tax_rate": "7.50",
        "discount_amount": "1000.00",
        "other_costs": "5000.00",
        "notes": "Net 30 supplier credit terms.",
        "items": [
            {
                "purchase_item_id": "8c2deb4d-4c8d-5cae-acde-3c1e8c4dcb7e",
                "invoice_quantity": 100,
                "unit_cost": "2500.00",
                "discount_amount": "500.00",
            }
        ],
    },
    request_only=True,
)

_purchase_invoice_response_example = OpenApiExample(
    "Supplier Invoice Representation",
    summary="Confirmed PurchaseInvoice with frozen vendor snapshot and line items",
    description="Full representation of a recorded `PurchaseInvoice` (UI Pages 1-3 & Page 7 Supplier Invoice tab).",
    value={
        "id": "9d3efc5e-5d9e-4dbf-bdef-4d2f9d5edc8f",
        "organization": "a1b2c3d4-e5f6-4789-b1c2-d3e4f5a6b7c8",
        "purchase": "7b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
        "purchase_number": "PO-2026-0004",
        "vendor": "1fd7d5fb-eb86-45be-a42e-d5cfd8396004",
        "warehouse": "d1328862-6a31-421b-94ac-dc25744f21fc",
        "recorded_by": "11111111-2222-3333-4444-555555555555",
        "recorded_by_name": "Admin User",
        "billed_to": "Funke Adeleke - RIINOX Operations",
        "invoice_type": "supplier_invoice",
        "vendor_company_name": "TechSupply Global Ltd",
        "vendor_contact_person": "Chinedu Okafor",
        "vendor_email": "billing@techsupply.ng",
        "vendor_phone": "+2348001112222",
        "vendor_address": "12 Wharf Road, Apapa, Lagos",
        "invoice_number": "PINV-2026-0001",
        "vendor_invoice_number": "SUP-INV-2026-0881",
        "issue_date": "2026-10-15",
        "due_date": "2026-11-15",
        "status": "confirmed",
        "subtotal": "249500.00",
        "discount_amount": "1000.00",
        "tax_rate": "7.50",
        "tax_amount": "18637.50",
        "other_costs": "5000.00",
        "total": "272137.50",
        "amount_paid": "0.00",
        "balance_due": "272137.50",
        "currency": "NGN",
        "supporting_document": None,
        "notes": "Net 30 supplier credit terms.",
        "items": [
            {
                "id": "ae4f0d6f-6eaf-4ec0-ce00-5e30ae6fed90",
                "purchase_item": "8c2deb4d-4c8d-5cae-acde-3c1e8c4dcb7e",
                "product": "1e5339e4-f1f9-4259-b849-70cf2c242e4c",
                "product_name": "Wireless Mouse",
                "variant": "e2f3a4b5-c6d7-4789-b1c2-d3e4f5a6b7c8",
                "variant_name": "Black",
                "description": "Wireless Mouse (Black)",
                "unit_name": "carton",
                "unit_multiplier": "12.000",
                "quantity_ordered": 100,
                "invoice_quantity": 100,
                "unit_cost": "2500.00",
                "discount_amount": "500.00",
                "line_total": "249500.00",
                "created_at": "2026-10-15T10:30:00Z",
            }
        ],
        "created_at": "2026-10-15T10:30:00Z",
        "updated_at": "2026-10-15T10:30:00Z",
    },
    response_only=True,
)

_purchase_payment_create_example = OpenApiExample(
    "Record Supplier Payment Request",
    summary="Record partial or full supplier payment",
    description=(
        "Payload from the 'Record Payment' modal (UI Page 15/20). "
        "If `amount` is omitted or null, defaults to the full remaining `balance_due`."
    ),
    value={
        "amount": "100000.00",
        "method": "bank_transfer",
        "reference": "NIP-TRF-20261016-9921",
        "payment_date": "2026-10-16",
        "notes": "First installment via corporate bank transfer.",
    },
    request_only=True,
)

_purchase_payment_response_example = OpenApiExample(
    "Supplier Payment Receipt",
    summary="Recorded PurchasePayment receipt and remaining balance",
    description="Representation of a recorded supplier payment in the Payments tab (UI Page 7 & 15).",
    value={
        "id": "bf501e70-7fb0-4fd1-df11-6f41bf70fe01",
        "receipt_number": "PRCP-2026-0001",
        "purchase": "7b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
        "payment": "c0612f81-80c1-40e2-e022-7052c0810f12",
        "amount": "100000.00",
        "balance_after": "172137.50",
        "method": "bank_transfer",
        "reference": "NIP-TRF-20261016-9921",
        "currency": "NGN",
        "payment_date": "2026-10-16",
        "notes": "First installment via corporate bank transfer.",
        "supporting_document": None,
        "recorded_by": "11111111-2222-3333-4444-555555555555",
        "recorded_by_name": "Admin User",
        "is_voided": False,
        "received_on": "2026-10-16T11:15:00Z",
        "created_at": "2026-10-16T11:15:00Z",
    },
    response_only=True,
)

_purchase_return_create_example = OpenApiExample(
    "Create Supplier Return Request",
    summary="Return damaged or excess received goods to supplier",
    description=(
        "Records a return of previously received items back to the vendor (UI Page 7 Returns tab). "
        "Deducts stock from warehouse inventory and posts double-entry financial ledger entries."
    ),
    value={
        "reason": "10 units arrived with damaged outer packaging during transit.",
        "items": [
            {
                "purchase_item": "8c2deb4d-4c8d-5cae-acde-3c1e8c4dcb7e",
                "quantity": 10,
                "batch": "d1723092-91d2-41f3-f133-8163d1921023",
            }
        ],
    },
    request_only=True,
)

_purchase_return_response_example = OpenApiExample(
    "Supplier Return Representation",
    summary="Confirmed PurchaseReturn record",
    description="Representation of a completed supplier return with line-level refund amounts.",
    value={
        "id": "e2834103-a2e3-4204-a244-9274e2032134",
        "return_number": "PRET-2026-0001",
        "organization": "a1b2c3d4-e5f6-4789-b1c2-d3e4f5a6b7c8",
        "purchase": "7b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
        "returned_by": "11111111-2222-3333-4444-555555555555",
        "returned_by_name": "Admin User",
        "reason": "10 units arrived with damaged outer packaging during transit.",
        "total_refund_amount": "25000.00",
        "items": [
            {
                "id": "f3945214-b3f4-4315-b355-a385f3143245",
                "purchase_item": "8c2deb4d-4c8d-5cae-acde-3c1e8c4dcb7e",
                "product_id": "1e5339e4-f1f9-4259-b849-70cf2c242e4c",
                "product_name": "Wireless Mouse",
                "variant_name": "Black",
                "batch": "d1723092-91d2-41f3-f133-8163d1921023",
                "batch_number": "BATCH-2026-10A",
                "quantity": 10,
                "unit_cost": "2500.00",
                "line_total": "25000.00",
                "created_at": "2026-10-17T09:00:00Z",
            }
        ],
        "created_at": "2026-10-17T09:00:00Z",
    },
    response_only=True,
)

_purchase_stock_activity_response_example = OpenApiExample(
    "Purchase Stock Activity Entries",
    summary="Immutable inventory ledger movements for receipts and returns",
    description="Chronological `InventoryLedgerEntry` records for the Purchase Order's Stock Activity tab (UI Page 7).",
    value=[
        {
            "id": "14a56325-c405-4426-c466-b49614254356",
            "transaction_id": "PRET-E2834103-9A1B2C",
            "transaction_type": "return",
            "product": "1e5339e4-f1f9-4259-b849-70cf2c242e4c",
            "product_name": "Wireless Mouse",
            "variant": "e2f3a4b5-c6d7-4789-b1c2-d3e4f5a6b7c8",
            "variant_name": "Black",
            "batch": "d1723092-91d2-41f3-f133-8163d1921023",
            "batch_number": "BATCH-2026-10A",
            "warehouse": "d1328862-6a31-421b-94ac-dc25744f21fc",
            "warehouse_name": "Main Warehouse - Lagos",
            "quantity_moved": -120,
            "unit_cost": "208.33",
            "created_at": "2026-10-17T09:00:00Z",
        },
        {
            "id": "25b67436-d516-4537-d577-c50725365467",
            "transaction_id": "PUR-7B1DEB4D-3F4E5D",
            "transaction_type": "purchase",
            "product": "1e5339e4-f1f9-4259-b849-70cf2c242e4c",
            "product_name": "Wireless Mouse",
            "variant": "e2f3a4b5-c6d7-4789-b1c2-d3e4f5a6b7c8",
            "variant_name": "Black",
            "batch": "d1723092-91d2-41f3-f133-8163d1921023",
            "batch_number": "BATCH-2026-10A",
            "warehouse": "d1328862-6a31-421b-94ac-dc25744f21fc",
            "warehouse_name": "Main Warehouse - Lagos",
            "quantity_moved": 600,
            "unit_cost": "208.33",
            "created_at": "2026-10-15T08:00:00Z",
        },
    ],
    response_only=True,
)

# PurchaseViewSet Schema Decorator

purchase_schema = extend_schema_view(
    list=extend_schema(
        tags=["Purchases"],
        summary="List Purchase Orders",
        description=(
            "### Overview\n"
            "Retrieves a paginated, filterable, and searchable list of purchase orders for the active tenant "
            "organization, including nested line items, supplier/warehouse names, payment status, and contextual "
            "`available_actions` (PRD §6, §18; UI Pages 4–6, 9–12).\n\n"
            "### Frontend Guide: Dynamic Action Gating (`available_actions`)\n"
            "Every purchase order object returns an `available_actions` string array computed by the backend state machine. "
            "Frontend engineers should use this array directly to render/enable options in the **Purchase Order List row kebab menu (`⋮`)** "
            "(UI Pages 5–6) and **Purchase Order Detail `Actions` dropdown** (UI Pages 13–14) instead of hardcoding status checks on the client.\n\n"
            "| Action Key | Backend State Condition | Target UI Action / API Endpoint | Required User Permission |\n"
            "| :--- | :--- | :--- | :--- |\n"
            "| `view` | Always returned | Open PO Detail (`GET /purchases/{id}/`) | `VIEW_PURCHASES` |\n"
            "| `edit` | `status != 'cancelled'` AND all items have `quantity_received == 0` | Edit PO modal (`PUT` / `PATCH /purchases/{id}/`) | `CREATE_PURCHASE` |\n"
            "| `cancel` | `status != 'cancelled'` AND all items have `quantity_received == 0` | Cancel Order modal (`POST /purchases/{id}/cancel/`) | `CREATE_PURCHASE` |\n"
            "| `delete` | `status != 'cancelled'` AND all items have `quantity_received == 0` | Delete PO confirmation (`DELETE /purchases/{id}/`) | `CREATE_PURCHASE` |\n"
            "| `receive_goods` | `status != 'cancelled'` AND any item has `quantity_received < quantity_ordered` | Receive Products modal (`POST /purchases/{id}/receive/`) | `RECEIVE_GOODS` |\n"
            "| `record_purchase_invoice` | `status != 'cancelled'` | 3-Step Supplier Invoice wizard (`POST /purchases/{id}/purchase-invoice/`) | `CREATE_PURCHASE` |\n"
            "| `record_payment` | `(has_received_goods OR has_active_invoice)` AND `balance_due > 0.00` | Record Payment modal (`POST /purchases/{id}/payments/`) | `CREATE_PURCHASE` |\n"
            "| `create_return` | Any item has `quantity_received > 0` | Return Goods modal (`POST /purchases/{id}/returns/`) | `CREATE_PURCHASE` |\n\n"
            "> **Frontend RBAC Intersection**: `available_actions` reflects **order lifecycle state**. Intersect it with the logged-in user's role capabilities "
            "(e.g., an `inventory-staff` user has `RECEIVE_GOODS` and `VIEW_PURCHASES`, so show `view` and `receive_goods`, while hiding `edit`/`cancel`/`record_payment` which require `CREATE_PURCHASE`).\n\n"
            "### Required Permissions & Access Control (PRD §28, §55)\n"
            "- Requires **`VIEW_PURCHASES`** permission in the target organization (granted to `owner`, `administrator`, `manager`, "
            "`inventory-manager`, `inventory-staff`, `accountant`, `finance-officer`).\n"
            "- Unauthenticated requests return HTTP 401.\n"
            "- Authenticated callers without active membership or `VIEW_PURCHASES` capability receive HTTP 403 Forbidden.\n\n"
            "### Multi-Tenancy & Tenant Boundaries (PRD §6)\n"
            "- Strictly scoped under `/api/organizations/{organization_id}/purchases/` via "
            "`Purchase.objects.for_organization(org).for_user(user).with_details()`.\n"
            "- Requesting purchase orders from an unauthorized organization returns HTTP 403/404.\n\n"
            "### Filtering, Search & Ordering\n"
            "- **Filters**: `?status=`, `?payment_status=`, `?vendor=`, `?warehouse=`, `?order_date_after=`, "
            "`?order_date_before=`, `?delivery_date_after=`, `?delivery_date_before=`.\n"
            "- **Search**: `?search=` matches `purchase_number`, `vendor__company_name`, `vendor_invoice_number`, and `notes`.\n"
            "- **Ordering**: `?ordering=` supports `created_at`, `order_date`, `delivery_date`, `purchase_number`, "
            "`status`, `payment_status`, and `amount_paid`.\n\n"
            "### Plan Limits & Subscription Invariants (PRD §10, §52)\n"
            "- Read operations do NOT consume monthly transaction allowances (`max_transactions_per_month`)."
        ),
        parameters=PURCHASE_LIST_FILTER_PARAMS,
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
            "Fetches complete details for a single purchase order by UUID, powering the **Purchase Order Detail** "
            "`Overview` and `Received Goods` tabs (UI Pages 7–14).\n\n"
            "### Frontend Guide: Financial Calculations & `balance_due` Priority\n"
            "- **`items[].ordered_value`**: `(quantity_ordered × purchase_cost) - item.discount` (floored at `0.00`).\n"
            "- **`items[].received_value`**: `(quantity_received × purchase_cost) - item.discount` (floored at `0.00`, `0.00` when `quantity_received == 0`).\n"
            "- **`subtotal`**: Sum of `items[].ordered_value` across all line items.\n"
            "- **`ordered_value`**: `subtotal + other_costs - discount` (total expected cost of the order).\n"
            "- **`total_value`**: Sum of `items[].received_value + other_costs - discount` (total cost of physically received goods).\n"
            "- **`balance_due`**: Remaining amount owed to the supplier (`max(payable_total - amount_paid, 0.00)`), where `payable_total` is resolved in priority order:\n"
            "  1. **Active `PurchaseInvoice.total`** (if a Supplier Invoice has been recorded via `POST /purchases/{id}/purchase-invoice/`, including tax, invoice discounts, and shipping).\n"
            "  2. **`total_value`** (if any goods have been received and no Supplier Invoice exists yet).\n"
            "  3. **`ordered_value`** (while the PO is still in `draft`/`ordered` with zero received items).\n\n"
            "### Dynamic Action Gating (`available_actions`)\n"
            "Returns the exact actions valid for the order's current lifecycle state:\n"
            "- **Before Goods Receipt (`draft` / `ordered`, `quantity_received == 0`)**:\n"
            "  `[\"view\", \"edit\", \"cancel\", \"delete\", \"receive_goods\", \"record_purchase_invoice\"]` "
            "( plus `\"record_payment\"` if a Supplier Invoice has already been recorded with `balance_due > 0`).\n"
            "- **Partially Received (`partially_received`, `0 < quantity_received < quantity_ordered`)**:\n"
            "  `[\"view\", \"receive_goods\", \"record_purchase_invoice\", \"record_payment\", \"create_return\"]` "
            "(`edit`, `cancel`, and `delete` are automatically removed to protect inventory/ledger integrity).\n"
            "- **Fully Received & Unpaid (`received`, `balance_due > 0`)**:\n"
            "  `[\"view\", \"record_purchase_invoice\", \"record_payment\", \"create_return\"]` (`receive_goods` is removed).\n"
            "- **Fully Received & Paid (`received`, `payment_status == 'paid'`, `balance_due == 0`)**:\n"
            "  `[\"view\", \"record_purchase_invoice\", \"create_return\"]` (`record_payment` is removed).\n"
            "- **Cancelled (`cancelled`)**:\n"
            "  `[\"view\"]`.\n\n"
            "### Required Permissions & Cross-Tenant Isolation (PRD §6, §28, §55)\n"
            "- Requires **`VIEW_PURCHASES`** permission in the target tenant organization.\n"
            "- Cross-tenant lookups return HTTP 404 Not Found to prevent resource enumeration across tenant boundaries."
        ),
        parameters=[PURCHASE_ORGANIZATION_PARAM, PURCHASE_ID_PARAM],
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
            "Creates a new Purchase Order and its nested line items in an atomic transaction, backing the "
            "**3-Step Create Purchase Order Wizard** (PRD §10, §18; UI Pages 4, 5, 8, 16–18).\n\n"
            "### Frontend Guide: Required vs. Optional Payload Fields & Auto-Defaults\n"
            "| Field | Required? | Default / Auto-Resolution Behavior |\n"
            "| :--- | :---: | :--- |\n"
            "| `vendor` | **Required** | UUID of an active supplier in this organization (`GET /api/vendors/`). |\n"
            "| `warehouse` | **Required** | UUID of an active destination warehouse in this organization (`GET /api/warehouses/`). |\n"
            "| `items` | Optional | Array of line items to order. |\n"
            "| `items[].product` | **Required** (per line) | UUID of the catalog product (`GET /api/products/products/`). |\n"
            "| `items[].quantity_ordered` | **Required** (per line) | Integer `>= 1`. |\n"
            "| `items[].purchase_cost` | Optional | If omitted or `null`, automatically snapshots `variant.purchase_cost` or `product.purchase_cost` (multiplied by `unit.multiplier` when `unit` is set). Pass explicitly to override with a negotiated unit cost. |\n"
            "| `items[].discount` | Optional | Defaults to `\"0.00\"`. |\n"
            "| `items[].variant` | Optional | UUID of `ProductVariant` (validated to belong to `items[].product`). |\n"
            "| `items[].unit` | Optional | UUID of `ProductUnitConversion` (validated to belong to `items[].product`). |\n"
            "| `other_costs` | Optional | Defaults to `\"0.00\"`. Typically recorded later on the Supplier Invoice (`POST /purchases/{id}/purchase-invoice/`), which automatically syncs back to the PO. |\n"
            "| `discount` | Optional | Defaults to `\"0.00\"`. Typically recorded later on the Supplier Invoice (`POST /purchases/{id}/purchase-invoice/`), which automatically syncs back to the PO. |\n"
            "| `vendor_invoice_number` | Optional | Defaults to `null`. Also auto-synced when a Supplier Invoice is recorded. |\n"
            "| `order_date` / `delivery_date` | Optional | `YYYY-MM-DD` dates (`null` if omitted). |\n"
            "| `delivery_method` / `notes` | Optional | Free-text strings (`null` if omitted). |\n"
            "| `status` | Optional | Defaults to `\"draft\"` (can also be initialized as `\"ordered\"`). |\n"
            "| `organization` | Optional | Auto-resolved from URL path `{organization_id}`. |\n"
            "| `ordered_by` | Optional | Auto-resolved from authenticated `request.user`. |\n\n"
            "### Atomic Creation & Inventory Separation Invariant (PRD §10, §18)\n"
            "1. Validates that `vendor`, `warehouse`, and every line item's `product`, `variant`, and `unit` "
            "belong to the active tenant organization.\n"
            "2. Generates a sequential `purchase_number` (`PO-YYYY-NNNN`) and initializes `status = 'draft'` "
            "(or `'ordered'`) and `payment_status = 'unpaid'`.\n"
            "3. Snapshots each line item's `purchase_cost`, `discount`, and packaging `unit` (`ProductUnitConversion`).\n"
            "4. **No Premature Stock Mutation**: Creating a Purchase Order does **NOT** increment warehouse inventory "
            "or post financial ledger entries. Stock and ledger entries are only created when physical goods are checked in "
            "via `POST /api/organizations/{organization_id}/purchases/{id}/receive/`.\n"
            "5. Records audit event `PURCHASE_ORDER_CREATED`.\n\n"
            "### Required Permissions & Access Control (PRD §28, §55)\n"
            "- Requires **`CREATE_PURCHASE`** permission in the target organization (roles: `owner`, `administrator`, `inventory-manager`).\n"
            "- Cross-tenant foreign keys (`vendor`, `warehouse`, or `items[].product` belonging to another tenant) "
            "are rejected with HTTP 400 Bad Request.\n\n"
            "### Plan Limits & Subscription Invariants (PRD §10, §52)\n"
            "- Enforces active organization subscription standing via `ensure_subscription_active(organization)`.\n"
            "- Inactive or expired subscriptions reject creation with HTTP 402/403 (`SUBSCRIPTION_INACTIVE`)."
        ),
        parameters=[PURCHASE_ORGANIZATION_PARAM],
        request=PurchaseCreateSerializer,
        examples=[_purchase_create_minimal_example, _purchase_create_example],
        responses={
            201: OpenApiResponse(
                response=PurchaseSerializer,
                description="Purchase order successfully created.",
                examples=[_purchase_response_example],
            ),
            400: OpenApiResponse(
                response=CodeDetailErrorSerializer,
                description="Validation error (e.g. cross-tenant vendor/warehouse/product or invalid quantity).",
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
            "Fully updates an existing Purchase Order and optionally replaces its nested `items` collection "
            "(PRD §18; UI Page 13).\n\n"
            "### Frontend Guide: Required vs. Optional Fields (`PUT` vs `PATCH`)\n"
            "- **`PUT`** requires `vendor` and `warehouse` in the payload (all other fields are optional).\n"
            "- If `items` is included in the payload, it **replaces** the entire line-item collection on the PO (only allowed while `quantity_received == 0`). "
            "Just like `POST`, `items[].purchase_cost` and `items[].discount` are optional and auto-resolve from the product catalog if omitted.\n"
            "- If `items` is omitted from the payload, existing line items remain untouched.\n\n"
            "### Business Rules & Immutability Guards (PRD §18)\n"
            "- **Cancelled Guard**: Cancelled purchase orders (`status == 'cancelled'`) cannot be modified.\n"
            "- **Received Goods Guard**: Once any line item has `quantity_received > 0`, changing `vendor`, `warehouse`, "
            "or replacing `items` is strictly prohibited and returns HTTP 400 (`PURCHASE_VALIDATION_ERROR`). Header metadata "
            "such as `notes`, `delivery_date`, or `vendor_invoice_number` may still be updated.\n"
            "- **Audit Trail**: Emits `PURCHASE_ORDER_APPROVED` if transitioning to `ORDERED`, otherwise `PURCHASE_ORDER_UPDATED`.\n\n"
            "### Required Permissions & Subscription Invariants (PRD §10, §28, §52)\n"
            "- Requires **`CREATE_PURCHASE`** permission (roles: `owner`, `administrator`, `inventory-manager`).\n"
            "- Enforces `ensure_subscription_active(organization)`."
        ),
        parameters=[PURCHASE_ORGANIZATION_PARAM, PURCHASE_ID_PARAM],
        request=PurchaseUpdateSerializer,
        examples=[_purchase_update_items_example],
        responses={
            200: OpenApiResponse(
                response=PurchaseSerializer,
                description="Purchase order successfully updated.",
            ),
            400: OpenApiResponse(
                response=CodeDetailErrorSerializer,
                description="Validation error or attempt to alter items/vendor/warehouse after goods receipt.",
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
            "Partially updates selected fields on a Purchase Order without requiring the full payload.\n\n"
            "### Frontend Guide: Required vs. Optional Fields (`PATCH`)\n"
            "- **All fields are optional** in `PATCH`. Send only the fields the user modified.\n"
            "- When `edit` is present in `available_actions` (`quantity_received == 0`), the frontend can `PATCH` any combination of header fields (`vendor`, `warehouse`, `order_date`, `delivery_date`, `delivery_method`, `other_costs`, `discount`, `status`, `notes`) and/or `items`.\n"
            "- Once goods have been partially or fully received (`edit` is no longer in `available_actions`), `vendor`, `warehouse`, and `items` are locked, while non-structural metadata (`notes`, `delivery_date`, `delivery_method`, `vendor_invoice_number`) can still be patched.\n\n"
            "### Business Rules & Immutability Guards (PRD §18)\n"
            "- Line items (`items`), `vendor`, and `warehouse` can only be modified while `quantity_received == 0` "
            "across all items.\n"
            "- Non-item metadata (`notes`, `delivery_date`, `delivery_method`, `vendor_invoice_number`) can be patched "
            "at any non-cancelled stage.\n\n"
            "### Required Permissions & Subscription Invariants (PRD §10, §28, §52)\n"
            "- Requires **`CREATE_PURCHASE`** permission (roles: `owner`, `administrator`, `inventory-manager`).\n"
            "- Enforces `ensure_subscription_active(organization)`."
        ),
        parameters=[PURCHASE_ORGANIZATION_PARAM, PURCHASE_ID_PARAM],
        request=PurchaseUpdateSerializer,
        examples=[_purchase_update_items_example, _purchase_partial_update_metadata_example],
        responses={
            200: OpenApiResponse(
                response=PurchaseSerializer,
                description="Purchase order partially updated successfully.",
            ),
            400: OpenApiResponse(
                response=CodeDetailErrorSerializer,
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
            "Hard-deletes an unfulfilled Purchase Order (PRD §18). Only enabled in the UI when `\"delete\"` is present in `available_actions`.\n\n"
            "### Data & Ledger Integrity Guards (PRD §18, §44)\n"
            "- **Received Goods Guard**: If any line item has `quantity_received > 0`, deletion is rejected with "
            "HTTP 400 (`CANNOT_DELETE_RECEIVED_PURCHASE`). Use the Supplier Return workflow (`POST /purchases/{id}/returns/`) "
            "to reverse received inventory.\n"
            "- **Active Payments Guard**: If any non-voided supplier payment (`PurchasePayment`) exists against the order, "
            "deletion is rejected with HTTP 400.\n"
            "- **Audit Trail**: Records `PURCHASE_ORDER_DELETED` prior to removal.\n\n"
            "### Required Permissions & Subscription Invariants\n"
            "- Requires **`CREATE_PURCHASE`** permission (roles: `owner`, `administrator`, `inventory-manager`).\n"
            "- Enforces `ensure_subscription_active(organization)`."
        ),
        parameters=[PURCHASE_ORGANIZATION_PARAM, PURCHASE_ID_PARAM],
        responses={
            204: OpenApiResponse(description="Purchase order successfully deleted."),
            400: OpenApiResponse(
                response=CodeDetailErrorSerializer,
                description="Deletion rejected because goods were already checked into inventory or payments exist.",
                examples=[
                    OpenApiExample(
                        "Cannot Delete Received Purchase",
                        value={
                            "code": "CANNOT_DELETE_RECEIVED_PURCHASE",
                            "detail": "Cannot delete a purchase order that has already received goods.",
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
    summary=extend_schema(
        tags=["Purchases"],
        summary="Purchase Order KPI Summary",
        description=(
            "### Overview\n"
            "Computes real-time KPI summary metrics for the active organization to populate the header cards on the "
            "**Purchase Order List** screen (UI Pages 4–6, 9–12).\n\n"
            "### Returned Metrics\n"
            "- **Order Counts**: `total_purchase_orders`, `pending_delivery_count` (`draft` + `ordered`), "
            "`partially_received_count`, `received_count`, `cancelled_count`.\n"
            "- **Payment Counts**: `unpaid_orders_count`, `partially_paid_orders_count`, `paid_orders_count` (excluding cancelled orders).\n"
            "- **Monetary Totals**: `total_spend` (value of received orders), `total_ordered_value`, `total_paid`, "
            "and `total_payable_balance`.\n\n"
            "### Required Permissions\n"
            "- Requires **`VIEW_PURCHASES`** permission in the target organization."
        ),
        parameters=[PURCHASE_ORGANIZATION_PARAM],
        responses={
            200: OpenApiResponse(
                response=PurchaseSummarySerializer,
                description="Purchase Order KPI summary retrieved successfully.",
                examples=[_purchase_summary_response_example],
            ),
            401: UNAUTHENTICATED_RESPONSE,
            403: FORBIDDEN_RESPONSE,
        },
    ),
    export=extend_schema(
        tags=["Purchases"],
        summary="Export Purchase Orders CSV",
        description=(
            "### Overview\n"
            "Streams a CSV download (`purchase_orders.csv`) of purchase orders matching the active query filters "
            "(UI Page 6 & 9 Export button).\n\n"
            "### Exported Columns\n"
            "`Purchase Number`, `Supplier`, `Warehouse`, `Order Date`, `Delivery Date`, `Status`, `Payment Status`, "
            "`Ordered Value`, `Total Received Value`, `Amount Paid`, `Balance Due`, `Vendor Invoice Number`, `Created At`.\n\n"
            "### Required Permissions\n"
            "- Requires **`VIEW_PURCHASES`** permission in the target organization."
        ),
        parameters=PURCHASE_LIST_FILTER_PARAMS,
        responses={
            200: OpenApiResponse(
                response=OpenApiTypes.BINARY,
                description="CSV file stream (`text/csv`).",
            ),
            401: UNAUTHENTICATED_RESPONSE,
            403: FORBIDDEN_RESPONSE,
        },
    ),
    cancel=extend_schema(
        tags=["Purchases"],
        summary="Cancel a Purchase Order",
        description=(
            "### Overview\n"
            "Cancels a `DRAFT` or `ORDERED` Purchase Order before any goods have been received (UI Page 14). "
            "Only enabled in the UI when `\"cancel\"` is present in `available_actions`.\n\n"
            "### Frontend Guide: Request Payload\n"
            "- `reason` (**Optional**, string): Reason entered in the Cancel Order confirmation modal (defaults to `\"\"` if omitted or `{}` is sent).\n\n"
            "### Business Rules & Invariants (PRD §18, §44)\n"
            "- **Status Guard**: Only `draft` or `ordered` purchase orders with zero received items (`quantity_received == 0`) "
            "and zero active payments can be cancelled.\n"
            "- If any goods have already been received, cancellation is rejected with HTTP 400 (`PURCHASE_CANCELLATION_ERROR`); "
            "use a Supplier Return (`POST /purchases/{id}/returns/`) instead.\n"
            "- **Cascading Supplier Invoice Void**: Any non-cancelled `PurchaseInvoice` attached to the purchase order is "
            "automatically marked `cancelled` / voided.\n"
            "- **Audit Trail**: Sets `status = 'cancelled'`, `cancelled_at`, `cancelled_by`, `cancel_reason`, and records "
            "`PURCHASE_ORDER_CANCELLED`. Subsequent responses return `available_actions: [\"view\"]`.\n\n"
            "### Required Permissions & Subscription Invariants\n"
            "- Requires **`CREATE_PURCHASE`** permission (roles: `owner`, `administrator`, `inventory-manager`).\n"
            "- Enforces `ensure_subscription_active(organization)`."
        ),
        parameters=[PURCHASE_ORGANIZATION_PARAM, PURCHASE_ID_PARAM],
        request=PurchaseCancelSerializer,
        examples=[_purchase_cancel_request_example],
        responses={
            200: OpenApiResponse(
                response=PurchaseSerializer,
                description="Purchase order cancelled successfully.",
            ),
            400: OpenApiResponse(
                response=CodeDetailErrorSerializer,
                description="Cancellation rejected (e.g. goods already received, active payments exist, or already cancelled).",
                examples=[
                    OpenApiExample(
                        "Cannot Cancel Received PO",
                        value={
                            "code": "PURCHASE_CANCELLATION_ERROR",
                            "detail": "Cannot cancel a purchase order that has already received goods. Use a supplier return instead.",
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
            "Records the physical arrival and check-in of ordered goods at the destination warehouse, backing the "
            "**Receive Products Modal** (PRD §10, §16, §18, §42, §43; UI Page 19). "
            "Enabled in the UI when `\"receive_goods\"` is present in `available_actions`.\n\n"
            "### Frontend Guide: Required vs. Optional Payload Fields\n"
            "| Field | Required? | Description |\n"
            "| :--- | :---: | :--- |\n"
            "| `items_received` | **Required** | Non-empty array of line items being checked in during this delivery. |\n"
            "| `items_received[].item_id` | **Required** | UUID of the `PurchaseItem` (`items[].id` from the Purchase Order response — **not** the catalog `product` UUID). |\n"
            "| `items_received[].quantity_received` | **Required** | Integer `>= 1`. Must not exceed remaining unreceived quantity (`quantity_ordered - quantity_received`). |\n"
            "| `items_received[].batch_number` | Optional | Batch string entered in the Receive Products modal (e.g. `\"BATCH-2026-10A\"`). Auto-creates or resolves a `ProductBatch`. |\n"
            "| `items_received[].expiry_date` | Optional | Expiry date (`YYYY-MM-DD`) for the batch. |\n"
            "| `items_received[].batch` | Optional | UUID of an existing `ProductBatch` if selecting an existing batch directly. |\n\n"
            "### Atomic Inventory, Batch & Double-Entry Ledger Workflow\n"
            "When executed inside its `@transaction.atomic` block:\n"
            "1. **Deterministic Row Locking**: Locks `Purchase` and `PurchaseItem` rows (`select_for_update()`) ordered by "
            "`(product_id, pk)` to prevent concurrent over-receiving race conditions (PRD §43).\n"
            "2. **Over-Receiving Guard**: Validates `item.quantity_received + quantity_received <= item.quantity_ordered`. "
            "Attempting to receive more than ordered aborts with HTTP 400 (`RECEIVE_ERROR`).\n"
            "3. **Packaging Unit Conversion**: Multiplies received quantities by `item.unit.multiplier` (default `1.000`) "
            "to compute base stock units (`base_quantity_moved`) and base unit cost (`purchase_cost / multiplier`).\n"
            "4. **Batch & Expiry Resolution (`products.services.resolve_or_create_product_batch`)**: If `batch_number` "
            "(`Batch No.`) or `expiry_date` (`Expiry Date`) is supplied per line item, resolves or creates the corresponding "
            "`ProductBatch` record.\n"
            "5. **Inventory & Immutable Ledger (`inventory.services.record_purchase_receipt_inventory_and_ledger`)**:\n"
            "   - Creates a unified `Transaction` (`transaction_type = 'purchase'`, `status = 'completed'`).\n"
            "   - Increments `Inventory.quantity` for `(product, variant, batch, warehouse)`.\n"
            "   - Writes positive `InventoryLedgerEntry` rows (visible in `GET /purchases/{id}/stock-activity/`).\n"
            "6. **Double-Entry Accounting (`transactions.services.record_purchase_financial_entries`)**:\n"
            "   - **Debit**: `INVENTORY_ASSET` (`received_cost_total`)\n"
            "   - **Credit**: `ACCOUNTS_PAYABLE` (`received_cost_total`)\n"
            "7. **Lifecycle Status & Action Transition**: Transitions `purchase.status` to `partially_received` or `received`, "
            "removes `edit`/`cancel`/`delete` from `available_actions`, unlocks `record_payment` and `create_return`, "
            "and records audit event `PURCHASE_ORDER_RECEIVED`.\n\n"
            "### Required Permissions & Subscription Invariants (PRD §10, §28, §52)\n"
            "- Requires **`RECEIVE_GOODS`** permission in the target organization (roles: `owner`, `administrator`, `inventory-manager`, `inventory-staff`).\n"
            "- Enforces `ensure_subscription_active(organization)` and consumes 1 monthly transaction slot "
            "(`UsageMetric.TRANSACTIONS`)."
        ),
        parameters=[PURCHASE_ORGANIZATION_PARAM, PURCHASE_ID_PARAM],
        request=PurchaseReceiveSerializer,
        examples=[_purchase_receive_request_example],
        responses={
            200: OpenApiResponse(
                response=PurchaseReceiveResponseSerializer,
                description="Goods successfully checked in, stock updated, and ledger entries created.",
                examples=[_purchase_receive_response_example],
            ),
            400: OpenApiResponse(
                response=CodeDetailErrorSerializer,
                description="Goods receipt validation failure (e.g. over-receiving, invalid item UUID, or cancelled PO).",
                examples=[
                    OpenApiExample(
                        "Over-Receiving Rejected",
                        value={
                            "code": "RECEIVE_ERROR",
                            "detail": "Cannot receive 150 units of 'Wireless Mouse'. Already received 0 of 100 ordered.",
                        },
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
    purchase_invoice=extend_schema(
        tags=["Purchases"],
        summary="List or Record Supplier Invoice (PurchaseInvoice)",
        description=(
            "### Overview\n"
            "- **`GET`**: Lists all supplier invoices (`PurchaseInvoice`) recorded for this purchase order, powering the "
            "**Supplier Invoice** detail tab (UI Page 7).\n"
            "- **`POST`**: Records or updates the active `PurchaseInvoice` for this purchase order, backing the "
            "**3-Step Create New Invoice (Supplier Invoice) Wizard** (UI Pages 1–3, 17). Enabled when `\"record_purchase_invoice\"` is in `available_actions`.\n\n"
            "### Frontend Guide: Required vs. Optional Payload Fields (`POST`)\n"
            "**All fields in the `POST` body are optional** — an empty `{}` body will generate a confirmed Supplier Invoice directly from the Purchase Order's current snapshot:\n\n"
            "| Field | Required? | Default / Synchronization Behavior |\n"
            "| :--- | :---: | :--- |\n"
            "| `vendor_invoice_number` | Optional | Supplier's external bill reference. If provided, **automatically syncs back to `purchase.vendor_invoice_number`**. |\n"
            "| `billed_to` | Optional | Entity name billed on the invoice (UI Step 1). |\n"
            "| `invoice_type` | Optional | Defaults to `\"supplier_invoice\"` (or `\"credit_note\"`, `\"debit_note\"`). |\n"
            "| `issue_date` / `due_date` | Optional | `YYYY-MM-DD` dates (`issue_date` defaults to today if omitted). |\n"
            "| `tax_rate` | Optional | Tax percentage (e.g. `\"7.50\"` for 7.5%). Defaults to `\"0.00\"`. |\n"
            "| `tax_amount` | Optional | Explicit tax amount override; if omitted, computed as `(subtotal - discount_amount) × (tax_rate / 100)`. |\n"
            "| `discount_amount` | Optional | Global invoice discount. Defaults to `purchase.discount`, and **automatically syncs back to `purchase.discount`** when provided. |\n"
            "| `other_costs` | Optional | Shipping/handling charges on the bill. Defaults to `purchase.other_costs`, and **automatically syncs back to `purchase.other_costs`** when provided. |\n"
            "| `items` | Optional | Per-line overrides (`purchase_item_id`, `invoice_quantity`, `unit_cost`, `discount_amount`). If omitted, lines auto-populate from the PO (`quantity_received` if > 0, else `quantity_ordered`). |\n\n"
            "### 3-Step Supplier Invoice Creation & Synchronization Workflow (`POST`)\n"
            "1. **Step 1 — Header & Party Details**: Captures `billed_to`, `invoice_type` (`supplier_invoice`), "
            "`vendor_invoice_number`, `issue_date`, `due_date`, `notes`, and freezes vendor contact details via "
            "`vendors.services.vendor_snapshot`.\n"
            "2. **Step 2 — Line Items & Tax Calculation**:\n"
            "   - Accepts optional per-line overrides (`items`: `purchase_item_id`, `invoice_quantity`, `unit_cost`, `discount_amount`).\n"
            "   - Computes `subtotal`, applies `discount_amount`, `tax_rate` / `tax_amount`, and `other_costs` to derive `total`.\n"
            "3. **Step 3 — Review, Confirmation & PO Sync**:\n"
            "   - Synchronizes `vendor_invoice_number`, `discount_amount` (`purchase.discount`), and `other_costs` (`purchase.other_costs`) back onto the parent `Purchase`.\n"
            "   - Updates `purchase.balance_due` to reflect the confirmed `PurchaseInvoice.total` (including tax).\n"
            "   - Unlocks `\"record_payment\"` in `purchase.available_actions` (even before goods arrive, enabling advance supplier payments against an invoice).\n"
            "   - Emits audit event `PURCHASE_INVOICE_RECORDED`.\n\n"
            "### Required Permissions\n"
            "- **`GET`**: Requires **`VIEW_PURCHASES`** permission.\n"
            "- **`POST`**: Requires **`CREATE_PURCHASE`** permission (roles: `owner`, `administrator`, `inventory-manager`) and an active organization subscription."
        ),
        parameters=[PURCHASE_ORGANIZATION_PARAM, PURCHASE_ID_PARAM],
        request=PurchaseInvoiceCreateSerializer,
        examples=[
            _purchase_invoice_create_example,
            _purchase_invoice_response_example,
        ],
        responses={
            200: OpenApiResponse(
                response=PurchaseInvoiceSerializer(many=True),
                description="List of supplier invoices for this purchase order.",
                examples=[_purchase_invoice_response_example],
            ),
            201: OpenApiResponse(
                response=PurchaseInvoiceSerializer,
                description="Supplier invoice recorded or updated successfully.",
                examples=[_purchase_invoice_response_example],
            ),
            400: OpenApiResponse(
                response=CodeDetailErrorSerializer,
                description="Validation error while recording supplier invoice (e.g. cancelled PO or unmatched line item).",
            ),
            401: UNAUTHENTICATED_RESPONSE,
            402: PLAN_LIMIT_EXCEEDED_RESPONSE,
            403: FORBIDDEN_RESPONSE,
            404: NOT_FOUND_RESPONSE,
        },
    ),
    payments=extend_schema(
        tags=["Purchases"],
        summary="List or Record Supplier Payments",
        description=(
            "### Overview\n"
            "- **`GET`**: Lists all supplier payment receipts (`PurchasePayment`) recorded against this purchase order, "
            "powering the **Payments** detail tab (UI Page 7).\n"
            "- **`POST`**: Records a partial or full supplier payment against this purchase order, backing the "
            "**Record Payment Modal** (UI Pages 15, 20). Enabled when `\"record_payment\"` is in `available_actions`.\n\n"
            "### Frontend Guide: Required vs. Optional Payload Fields (`POST`)\n"
            "| Field | Required? | Default / Auto-Resolution Behavior |\n"
            "| :--- | :---: | :--- |\n"
            "| `method` | **Required** | One of `\"cash\"`, `\"bank_transfer\"`, `\"pos\"`, `\"cheque\"`, `\"mobile_money\"`, `\"card\"`, `\"other\"`. |\n"
            "| `amount` | Optional | Decimal amount paid (`> 0.00` and `<= balance_due`). **If omitted or `null`, automatically pays the full remaining `balance_due`**. |\n"
            "| `reference` | Optional | Bank transfer / POS / cheque reference string. |\n"
            "| `payment_date` | Optional | `YYYY-MM-DD` date the supplier was paid. |\n"
            "| `notes` | Optional | Internal payment notes. |\n"
            "| `supporting_document` | Optional | Multipart file upload for proof of payment receipt. |\n\n"
            "### Supplier Payment & Double-Entry Accounting Workflow (`POST`)\n"
            "1. **Payable Eligibility Guard**: Requires that goods have been received (`quantity_received > 0`) or an active "
            "`PurchaseInvoice` has been recorded. Cancelled or unreceived draft orders without an invoice cannot take payments.\n"
            "2. **Payable Total & Auto 'Pay in Full'**:\n"
            "   - Resolves `payable_total` from the active `PurchaseInvoice.total` if one exists, otherwise `purchase.total_value`.\n"
            "   - If `amount` is omitted or null, automatically defaults to the remaining `balance_due` (`payable_total - amount_paid`).\n"
            "   - Rejects overpayments (`amount > balance_due`) with HTTP 400 (`PAYMENT_VALIDATION_ERROR`).\n"
            "3. **Atomic Persistence & Synchronization**:\n"
            "   - Creates `Payment` and `PurchasePayment` (`PRCP-YYYY-NNNN`) with `payment_date`, `notes`, `reference`, and `balance_after`.\n"
            "   - Updates `purchase.amount_paid` and transitions `purchase.payment_status` to `partially_paid` or `paid` (removing `\"record_payment\"` from `available_actions` once `balance_due == 0.00`).\n"
            "   - Synchronizes `PurchaseInvoice.amount_paid` and `PurchaseInvoice.status` via `apply_payment_to_purchase_invoice`.\n"
            "4. **Double-Entry Financial Ledger (`TransactionTypeChoices.PAYMENT_SENT`)**:\n"
            "   - **Debit**: `ACCOUNTS_PAYABLE` (`paid_amount`)\n"
            "   - **Credit**: `CASH_BANK` (`paid_amount`)\n"
            "5. **Audit Trail**: Emits `PURCHASE_PAYMENT_RECORDED`.\n\n"
            "### Required Permissions & Subscription Invariants\n"
            "- **`GET`**: Requires **`VIEW_PURCHASES`** permission.\n"
            "- **`POST`**: Requires **`CREATE_PURCHASE`** permission (roles: `owner`, `administrator`, `inventory-manager`), an active subscription, and consumes 1 monthly transaction slot."
        ),
        parameters=[PURCHASE_ORGANIZATION_PARAM, PURCHASE_ID_PARAM],
        request=PurchasePaymentCreateSerializer,
        examples=[
            _purchase_payment_create_example,
            _purchase_payment_response_example,
        ],
        responses={
            200: OpenApiResponse(
                response=PurchasePaymentSerializer(many=True),
                description="List of supplier payments for this purchase order.",
                examples=[_purchase_payment_response_example],
            ),
            201: OpenApiResponse(
                response=PurchasePaymentSerializer,
                description="Supplier payment recorded successfully.",
                examples=[_purchase_payment_response_example],
            ),
            400: OpenApiResponse(
                response=CodeDetailErrorSerializer,
                description="Payment validation failure (e.g. overpayment, already paid, or unreceived purchase order).",
            ),
            401: UNAUTHENTICATED_RESPONSE,
            402: PLAN_LIMIT_EXCEEDED_RESPONSE,
            403: FORBIDDEN_RESPONSE,
            404: NOT_FOUND_RESPONSE,
        },
    ),
    returns=extend_schema(
        tags=["Purchases"],
        summary="List or Create Supplier Returns",
        description=(
            "### Overview\n"
            "- **`GET`**: Lists all supplier returns (`PurchaseReturn`) recorded against this purchase order, powering the "
            "**Returns** detail tab (UI Page 7).\n"
            "- **`POST`**: Records a return of damaged, defective, or excess goods back to the supplier (UI Pages 7, 10, 18). "
            "Enabled when `\"create_return\"` is in `available_actions`.\n\n"
            "### Frontend Guide: Required vs. Optional Payload Fields (`POST`)\n"
            "| Field | Required? | Description |\n"
            "| :--- | :---: | :--- |\n"
            "| `items` | **Required** | Non-empty array of received purchase items being returned to the supplier. |\n"
            "| `items[].purchase_item` | **Required** | UUID of the `PurchaseItem` (`items[].id` from the Purchase Order response — **not** the catalog `product` UUID). |\n"
            "| `items[].quantity` | **Required** | Integer `>= 1`. Must be `<= quantity_received - already_returned` and available in warehouse inventory. |\n"
            "| `items[].batch` | Optional | UUID of the specific `ProductBatch` being returned (obtainable from `GET /purchases/{id}/stock-activity/`). |\n"
            "| `reason` | Optional | Reason for returning the goods (defaults to `\"\"`). |\n\n"
            "### Supplier Return, Stock Deduction & Double-Entry Reversal Workflow (`POST`)\n"
            "1. **Returnable Quantity Validation**: Verifies for each line that `quantity <= item.quantity_received - already_returned`. "
            "Returning more than received is rejected with HTTP 400 (`PURCHASE_RETURN_ERROR`).\n"
            "2. **Warehouse Stock Deduction (`inventory.services.process_purchase_return_inventory_and_ledger`)**:\n"
            "   - Locks warehouse `Inventory` rows (`select_for_update()`), converts returned units using `item.unit.multiplier`, "
            "and deducts the returned base quantity (raising HTTP 400 if current warehouse stock is insufficient).\n"
            "   - Creates a `Transaction` (`transaction_type = 'return'`, `status = 'completed'`) and writes negative "
            "`InventoryLedgerEntry` rows (`quantity_moved = -base_qty`).\n"
            "3. **Double-Entry Financial Reversal (`transactions.services.record_purchase_return_financial_entries`)**:\n"
            "   - **Debit**: `ACCOUNTS_PAYABLE` (`total_refund_amount`)\n"
            "   - **Credit**: `INVENTORY_ASSET` (`total_refund_amount`)\n"
            "4. **Audit Trail**: Generates sequential `return_number` (`PRET-YYYY-NNNN`) and emits `PURCHASE_RETURN_CREATED`.\n\n"
            "### Required Permissions & Subscription Invariants\n"
            "- **`GET`**: Requires **`VIEW_PURCHASES`** permission.\n"
            "- **`POST`**: Requires **`CREATE_PURCHASE`** permission (roles: `owner`, `administrator`, `inventory-manager`), an active subscription, and consumes 1 monthly transaction slot."
        ),
        parameters=[PURCHASE_ORGANIZATION_PARAM, PURCHASE_ID_PARAM],
        request=PurchaseReturnCreateSerializer,
        examples=[
            _purchase_return_create_example,
            _purchase_return_response_example,
        ],
        responses={
            200: OpenApiResponse(
                response=PurchaseReturnSerializer(many=True),
                description="List of supplier returns for this purchase order.",
                examples=[_purchase_return_response_example],
            ),
            201: OpenApiResponse(
                response=PurchaseReturnSerializer,
                description="Supplier return recorded successfully, stock deducted, and ledger updated.",
                examples=[_purchase_return_response_example],
            ),
            400: OpenApiResponse(
                response=CodeDetailErrorSerializer,
                description="Return validation failure (e.g. returning more than received or insufficient stock in warehouse).",
            ),
            401: UNAUTHENTICATED_RESPONSE,
            402: PLAN_LIMIT_EXCEEDED_RESPONSE,
            403: FORBIDDEN_RESPONSE,
            404: NOT_FOUND_RESPONSE,
        },
    ),
    stock_activity=extend_schema(
        tags=["Purchases"],
        summary="List Purchase Stock Activity",
        description=(
            "### Overview\n"
            "Retrieves all immutable inventory ledger movements (`InventoryLedgerEntry`) associated with this "
            "Purchase Order (`transaction_type = 'purchase'`) and any of its Supplier Returns (`transaction_type = 'return'`), "
            "powering the **Stock Activity** detail tab (UI Page 7).\n\n"
            "### Returned Ledger Fields\n"
            "- `transaction_id`, `transaction_type`, `product`, `product_name`, `variant`, `variant_name`, "
            "`batch`, `batch_number`, `warehouse`, `warehouse_name`, `quantity_moved` (positive for receipts, negative for returns), "
            "`unit_cost`, and `created_at`.\n"
            "- Frontend Tip: The `batch` UUID returned on receipt rows can be passed to `POST /purchases/{id}/returns/` (`items[].batch`) "
            "when returning goods from a specific batch.\n\n"
            "### Required Permissions\n"
            "- Requires **`VIEW_PURCHASES`** permission in the target organization."
        ),
        parameters=[PURCHASE_ORGANIZATION_PARAM, PURCHASE_ID_PARAM],
        responses={
            200: OpenApiResponse(
                response=PurchaseStockActivitySerializer(many=True),
                description="Stock activity ledger entries for this purchase order.",
                examples=[_purchase_stock_activity_response_example],
            ),
            401: UNAUTHENTICATED_RESPONSE,
            403: FORBIDDEN_RESPONSE,
            404: NOT_FOUND_RESPONSE,
        },
    ),
)
