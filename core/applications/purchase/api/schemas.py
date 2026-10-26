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

_purchase_create_example = OpenApiExample(
    "Standard Purchase Order with Line Items",
    summary="Create PO with Product UUIDs, packaging units, and line items",
    description=(
        "Full purchase order creation payload including header info, supplier/vendor, destination warehouse, "
        "and line items. Each line item requires the catalog Product UUID (obtained from "
        "`GET /api/organizations/{organization_id}/products/`). Snapshots purchase costs at order time. "
        "Does NOT update inventory until `/receive/` is called."
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
    description=(
        "Full representation of a purchase order with calculated totals, order status, "
        "payment status, contextual `available_actions`, and item lines."
    ),
    value={
        "id": "7b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
        "purchase_number": "PO-2026-0004",
        "organization": "a1b2c3d4-e5f6-4789-b1c2-d3e4f5a6b7c8",
        "vendor": "f1e2d3c4-b5a6-4789-b1c2-d3e4f5a6b7c8",
        "vendor_name": "TechSupply Global Ltd",
        "warehouse": "c1d2e3f4-a5b6-4789-b1c2-d3e4f5a6b7c8",
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
                "product": "d1e2f3a4-b5c6-4789-b1c2-d3e4f5a6b7c8",
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
        "vendor": "f1e2d3c4-b5a6-4789-b1c2-d3e4f5a6b7c8",
        "warehouse": "c1d2e3f4-a5b6-4789-b1c2-d3e4f5a6b7c8",
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
                "product": "d1e2f3a4-b5c6-4789-b1c2-d3e4f5a6b7c8",
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
                "product_id": "d1e2f3a4-b5c6-4789-b1c2-d3e4f5a6b7c8",
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
            "product": "d1e2f3a4-b5c6-4789-b1c2-d3e4f5a6b7c8",
            "product_name": "Wireless Mouse",
            "variant": "e2f3a4b5-c6d7-4789-b1c2-d3e4f5a6b7c8",
            "variant_name": "Black",
            "batch": "d1723092-91d2-41f3-f133-8163d1921023",
            "batch_number": "BATCH-2026-10A",
            "warehouse": "c1d2e3f4-a5b6-4789-b1c2-d3e4f5a6b7c8",
            "warehouse_name": "Main Warehouse - Lagos",
            "quantity_moved": -120,
            "unit_cost": "208.33",
            "created_at": "2026-10-17T09:00:00Z",
        },
        {
            "id": "25b67436-d516-4537-d577-c50725365467",
            "transaction_id": "PUR-7B1DEB4D-3F4E5D",
            "transaction_type": "purchase",
            "product": "d1e2f3a4-b5c6-4789-b1c2-d3e4f5a6b7c8",
            "product_name": "Wireless Mouse",
            "variant": "e2f3a4b5-c6d7-4789-b1c2-d3e4f5a6b7c8",
            "variant_name": "Black",
            "batch": "d1723092-91d2-41f3-f133-8163d1921023",
            "batch_number": "BATCH-2026-10A",
            "warehouse": "c1d2e3f4-a5b6-4789-b1c2-d3e4f5a6b7c8",
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
            "### Required Permissions & Access Control (PRD §28, §55)\n"
            "- Requires **`VIEW_PURCHASES`** permission in the target organization.\n"
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
            "### Returned Data & Computed Properties\n"
            "- **Header & Party Details**: `purchase_number`, `vendor_name`, `warehouse_name`, `ordered_by_name`, "
            "`received_by_name`, `cancelled_by_name`, `order_date`, `delivery_date`, `delivery_method`.\n"
            "- **Financial Totals**: `subtotal`, `discount`, `other_costs`, `ordered_value`, `total_value` (received value), "
            "`amount_paid`, `balance_due` (uses active `PurchaseInvoice.total` if recorded, else `total_value` or `ordered_value`), "
            "and `payment_status`.\n"
            "- **Dynamic Action Gating (`available_actions`)**: Returns the exact row/detail actions valid for the "
            "order's current state (`view`, `edit`, `cancel`, `delete`, `receive_goods`, `record_purchase_invoice`, "
            "`record_payment`, `create_return`).\n\n"
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
            "- Requires **`CREATE_PURCHASE`** permission in the target organization.\n"
            "- Cross-tenant foreign keys (`vendor`, `warehouse`, or `items[].product` belonging to another tenant) "
            "are rejected with HTTP 400 Bad Request.\n\n"
            "### Plan Limits & Subscription Invariants (PRD §10, §52)\n"
            "- Enforces active organization subscription standing via `ensure_subscription_active(organization)`.\n"
            "- Inactive or expired subscriptions reject creation with HTTP 402/403 (`SUBSCRIPTION_INACTIVE`)."
        ),
        parameters=[PURCHASE_ORGANIZATION_PARAM],
        request=PurchaseCreateSerializer,
        examples=[_purchase_create_example],
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
            "### Business Rules & Immutability Guards (PRD §18)\n"
            "- **Cancelled Guard**: Cancelled purchase orders (`status == 'cancelled'`) cannot be modified.\n"
            "- **Received Goods Guard**: Once any line item has `quantity_received > 0`, changing `vendor`, `warehouse`, "
            "or replacing `items` is strictly prohibited and returns HTTP 400 (`PURCHASE_VALIDATION_ERROR`). Header metadata "
            "such as `notes`, `delivery_date`, or `vendor_invoice_number` may still be updated.\n"
            "- **Audit Trail**: Emits `PURCHASE_ORDER_APPROVED` if transitioning to `ORDERED`, otherwise `PURCHASE_ORDER_UPDATED`.\n\n"
            "### Required Permissions & Subscription Invariants (PRD §10, §28, §52)\n"
            "- Requires **`CREATE_PURCHASE`** permission.\n"
            "- Enforces `ensure_subscription_active(organization)`."
        ),
        parameters=[PURCHASE_ORGANIZATION_PARAM, PURCHASE_ID_PARAM],
        request=PurchaseCreateSerializer,
        examples=[_purchase_create_example, _purchase_update_items_example],
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
            "### Business Rules & Immutability Guards (PRD §18)\n"
            "- Line items (`items`), `vendor`, and `warehouse` can only be modified while `quantity_received == 0` "
            "across all items.\n"
            "- Non-item metadata (`notes`, `delivery_date`, `delivery_method`, `vendor_invoice_number`) can be patched "
            "at any non-cancelled stage.\n\n"
            "### Required Permissions & Subscription Invariants (PRD §10, §28, §52)\n"
            "- Requires **`CREATE_PURCHASE`** permission.\n"
            "- Enforces `ensure_subscription_active(organization)`."
        ),
        parameters=[PURCHASE_ORGANIZATION_PARAM, PURCHASE_ID_PARAM],
        request=PurchaseCreateSerializer,
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
            "Hard-deletes an unfulfilled Purchase Order (PRD §18).\n\n"
            "### Data & Ledger Integrity Guards (PRD §18, §44)\n"
            "- **Received Goods Guard**: If any line item has `quantity_received > 0`, deletion is rejected with "
            "HTTP 400 (`CANNOT_DELETE_RECEIVED_PURCHASE`). Use the Supplier Return workflow (`POST /purchases/{id}/returns/`) "
            "to reverse received inventory.\n"
            "- **Active Payments Guard**: If any non-voided supplier payment (`PurchasePayment`) exists against the order, "
            "deletion is rejected with HTTP 400.\n"
            "- **Audit Trail**: Records `PURCHASE_ORDER_DELETED` prior to removal.\n\n"
            "### Required Permissions & Subscription Invariants\n"
            "- Requires **`CREATE_PURCHASE`** permission.\n"
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
            "Cancels a `DRAFT` or `ORDERED` Purchase Order before any goods have been received (UI Page 14).\n\n"
            "### Business Rules & Invariants (PRD §18, §44)\n"
            "- **Status Guard**: Only `draft` or `ordered` purchase orders with zero received items (`quantity_received == 0`) "
            "and zero active payments can be cancelled.\n"
            "- If any goods have already been received, cancellation is rejected with HTTP 400 (`PURCHASE_CANCELLATION_ERROR`); "
            "use a Supplier Return (`POST /purchases/{id}/returns/`) instead.\n"
            "- **Cascading Supplier Invoice Void**: Any non-cancelled `PurchaseInvoice` attached to the purchase order is "
            "automatically marked `cancelled` / voided.\n"
            "- **Audit Trail**: Sets `status = 'cancelled'`, `cancelled_at`, `cancelled_by`, `cancel_reason`, and records "
            "`PURCHASE_ORDER_CANCELLED`.\n\n"
            "### Required Permissions & Subscription Invariants\n"
            "- Requires **`CREATE_PURCHASE`** permission.\n"
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
            "**Receive Products Modal** (PRD §10, §16, §18, §42, §43; UI Page 19).\n\n"
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
            "   - Writes positive `InventoryLedgerEntry` rows.\n"
            "6. **Double-Entry Accounting (`transactions.services.record_purchase_financial_entries`)**:\n"
            "   - **Debit**: `INVENTORY_ASSET` (`received_cost_total`)\n"
            "   - **Credit**: `ACCOUNTS_PAYABLE` (`received_cost_total`)\n"
            "7. **Lifecycle Status Transition**: Transitions `purchase.status` to `partially_received` or `received` "
            "and records audit event `PURCHASE_ORDER_RECEIVED`.\n\n"
            "### Required Permissions & Subscription Invariants (PRD §10, §28, §52)\n"
            "- Requires **`RECEIVE_GOODS`** permission in the target organization.\n"
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
            "**3-Step Create New Invoice (Supplier Invoice) Wizard** (UI Pages 1–3, 17).\n\n"
            "### 3-Step Supplier Invoice Creation & Snapshot Workflow (`POST`)\n"
            "1. **Step 1 — Header & Party Details**: Captures `billed_to`, `invoice_type` (`supplier_invoice`), "
            "`vendor_invoice_number`, `issue_date`, `due_date`, `notes`, and freezes vendor contact details via "
            "`vendors.services.vendor_snapshot`.\n"
            "2. **Step 2 — Line Items & Tax Calculation**:\n"
            "   - Accepts optional per-line overrides (`items`: `purchase_item_id`, `invoice_quantity`, `unit_cost`, `discount_amount`).\n"
            "   - If `items` is omitted, automatically populates lines from the Purchase Order snapshot (`quantity_received` if > 0, else `quantity_ordered`).\n"
            "   - Computes `subtotal`, applies `discount_amount`, `tax_rate` / `tax_amount`, and `other_costs` to derive `total`.\n"
            "3. **Step 3 — Review & Confirmation**: Synchronizes `amount_paid` from the purchase order, sets status to "
            "`confirmed` / `partially_paid` / `paid`, and emits audit event `PURCHASE_INVOICE_RECORDED`.\n\n"
            "### Required Permissions\n"
            "- **`GET`**: Requires **`VIEW_PURCHASES`** permission.\n"
            "- **`POST`**: Requires **`CREATE_PURCHASE`** permission and an active organization subscription."
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
            "**Record Payment Modal** (UI Pages 15, 20).\n\n"
            "### Supplier Payment & Double-Entry Accounting Workflow (`POST`)\n"
            "1. **Payable Eligibility Guard**: Requires that goods have been received (`quantity_received > 0`) or an active "
            "`PurchaseInvoice` has been recorded. Cancelled or unreceived draft orders without an invoice cannot take payments.\n"
            "2. **Payable Total & Auto 'Pay in Full'**:\n"
            "   - Resolves `payable_total` from the active `PurchaseInvoice.total` if one exists, otherwise `purchase.total_value`.\n"
            "   - If `amount` is omitted or null, automatically defaults to the remaining `balance_due` (`payable_total - amount_paid`).\n"
            "   - Rejects overpayments (`amount > balance_due`) with HTTP 400 (`PAYMENT_VALIDATION_ERROR`).\n"
            "3. **Atomic Persistence & Synchronization**:\n"
            "   - Creates `Payment` and `PurchasePayment` (`PRCP-YYYY-NNNN`) with `payment_date`, `notes`, `reference`, and `balance_after`.\n"
            "   - Updates `purchase.amount_paid` and transitions `purchase.payment_status` to `partially_paid` or `paid`.\n"
            "   - Synchronizes `PurchaseInvoice.amount_paid` and `PurchaseInvoice.status` via `apply_payment_to_purchase_invoice`.\n"
            "4. **Double-Entry Financial Ledger (`TransactionTypeChoices.PAYMENT_SENT`)**:\n"
            "   - **Debit**: `ACCOUNTS_PAYABLE` (`paid_amount`)\n"
            "   - **Credit**: `CASH_BANK` (`paid_amount`)\n"
            "5. **Audit Trail**: Emits `PURCHASE_PAYMENT_RECORDED`.\n\n"
            "### Required Permissions & Subscription Invariants\n"
            "- **`GET`**: Requires **`VIEW_PAYMENTS`** permission.\n"
            "- **`POST`**: Requires **`RECORD_PAYMENT`** permission, an active subscription, and consumes 1 monthly transaction slot."
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
            "- **`POST`**: Records a return of damaged, defective, or excess goods back to the supplier (UI Pages 7, 10, 18).\n\n"
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
            "- **`POST`**: Requires **`RECEIVE_GOODS`** permission, an active subscription, and consumes 1 monthly transaction slot."
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
            "`unit_cost`, and `created_at`.\n\n"
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
