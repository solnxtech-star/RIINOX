from drf_spectacular.utils import extend_schema, extend_schema_view, OpenApiResponse, OpenApiExample
from core.applications.purchase.api.serializers import PurchaseReceiveSerializer

_purchase_create_example = OpenApiExample(
    "Standard Purchase Order Creation",
    value={
        "organization": "a1b2c3d4e5f64789b1c2d3e4f5a6b7c8",
        "vendor": "f1e2d3c4b5a64789b1c2d3e4f5a6b7c8",
        "warehouse": "c1d2e3f4a5b64789b1c2d3e4f5a6b7c8",
        "ordered_by": "b1a2c3d4e5f64789b1c2d3e4f5a6b7c8",
        "delivery_method": "Supplier Delivery",
        "other_costs": 50000.00,
        "discount": 0.00,
        "order_date": "2026-10-08",
        "delivery_date": "2026-10-15",
        "notes": "Urgent order for Q4 restocking."
    },
    request_only=True,
)

_purchase_receive_example = OpenApiExample(
    "Partial or Full Goods Receipt",
    description="Records the physical receipt of goods from a supplier against the purchase order, which impacts the actual inventory ledger.",
    value={
        "items_received": [
            {
                "item_id": "9b1deb4d3b7d4bad9bdd2b0d7b3dcb6d",
                "quantity_received": 50
            }
        ]
    },
    request_only=True,
)

purchase_schema = extend_schema_view(
    list=extend_schema(
        tags=['Purchases'],
        summary="List Purchase Orders",
        description="Returns a paginated list of all purchase orders for the authenticated user's organization. Supports filtering by vendor, status, and date range."
    ),
    create=extend_schema(
        tags=['Purchases'],
        summary="Create a Purchase Order",
        description="Creates a new Purchase Order (supplier invoice equivalent). The system does NOT generate an invoice for the vendor. Instead, you record their invoice against this purchase. Orders do NOT impact the inventory ledger until the goods are received.",
        examples=[_purchase_create_example]
    ),
    retrieve=extend_schema(
        tags=['Purchases'],
        summary="Retrieve a Purchase Order",
        description="Fetch detailed information about a single purchase order, including all associated line items, receiving history, and payments."
    ),
    update=extend_schema(
        tags=['Purchases'],
        summary="Update a Purchase Order",
        description="Fully replace all details of a draft purchase order. Note that confirmed or received orders cannot have their core items updated directly."
    ),
    partial_update=extend_schema(
        tags=['Purchases'],
        summary="Partially Update a Purchase Order",
        description="Update specific fields of a purchase order, like attaching a `vendor_invoice_number` after it has been received from the supplier."
    ),
    destroy=extend_schema(
        tags=['Purchases'],
        summary="Delete a Purchase Order",
        description="Permanently deletes a draft purchase order. Orders that have already received goods or registered payments cannot be deleted, but they can be cancelled/voided."
    ),
    receive=extend_schema(
        tags=['Purchases'],
        summary="Receive Goods",
        description="Records the actual quantities received from the supplier for specific line items in this purchase order. This action generates Inventory Ledger Entries and updates the product's `current_stock`.",
        request=PurchaseReceiveSerializer,
        examples=[_purchase_receive_example],
        responses={
            200: OpenApiResponse(description="Purchase Received and Inventory Updated Successfully."),
            400: OpenApiResponse(description="Validation error (e.g., trying to receive more items than ordered, or item_id does not exist on this purchase).")
        }
    )
)
