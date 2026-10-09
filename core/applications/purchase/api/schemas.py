from drf_spectacular.utils import extend_schema, extend_schema_view, OpenApiResponse, OpenApiExample
from core.applications.purchase.api.serializers import (
    PurchaseSerializer,
    PurchaseCreateSerializer,
    PurchaseReceiveSerializer,
    PurchaseReceiveResponseSerializer,
)

_purchase_create_example = OpenApiExample(
    "Standard Purchase Order with Line Items",
    description="Creates a Purchase Order with supplier header details and line items. Does NOT update inventory until goods are received via /receive/.",
    value={
        "organization": "a1b2c3d4-e5f6-4789-b1c2-d3e4f5a6b7c8",
        "vendor": "f1e2d3c4-b5a6-4789-b1c2-d3e4f5a6b7c8",
        "warehouse": "c1d2e3f4-a5b6-4789-b1c2-d3e4f5a6b7c8",
        "delivery_method": "Supplier Delivery",
        "other_costs": "5000.00",
        "discount": "0.00",
        "order_date": "2026-10-09",
        "delivery_date": "2026-10-15",
        "notes": "Restocking core warehouse items.",
        "items": [
            {
                "product": "d1e2f3a4-b5c6-4789-b1c2-d3e4f5a6b7c8",
                "quantity_ordered": 100,
                "purchase_cost": "2500.00",
                "discount": "0.00"
            }
        ]
    },
    request_only=True,
)

_purchase_receive_example = OpenApiExample(
    "Partial or Full Goods Receipt",
    description="Records the physical receipt of goods from a supplier against specific line items. This creates a Transaction, inserts an InventoryLedgerEntry, updates warehouse stock, and updates purchase status.",
    value={
        "items_received": [
            {
                "item_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
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
        description="Returns a paginated list of all purchase orders for the authenticated user's organization. Supports optional filtering by ?organization=<id>.",
        responses={200: PurchaseSerializer(many=True)},
    ),
    create=extend_schema(
        tags=['Purchases'],
        summary="Create a Purchase Order",
        description="Creates a new Purchase Order header and associated line items. The order is created in 'draft' status and does NOT alter inventory stock.",
        request=PurchaseCreateSerializer,
        examples=[_purchase_create_example],
        responses={201: PurchaseSerializer},
    ),
    retrieve=extend_schema(
        tags=['Purchases'],
        summary="Retrieve a Purchase Order",
        description="Fetch detailed information about a single purchase order, including all associated line items, received quantities, and calculated total/ordered values.",
        responses={200: PurchaseSerializer},
    ),
    update=extend_schema(
        tags=['Purchases'],
        summary="Update a Purchase Order",
        description="Replace all details of a draft purchase order. Line items cannot be replaced if goods have already been received against the order.",
        request=PurchaseCreateSerializer,
        responses={200: PurchaseSerializer},
    ),
    partial_update=extend_schema(
        tags=['Purchases'],
        summary="Partially Update a Purchase Order",
        description="Update specific fields of a purchase order (e.g. attaching a `vendor_invoice_number` or notes).",
        request=PurchaseCreateSerializer,
        responses={200: PurchaseSerializer},
    ),
    destroy=extend_schema(
        tags=['Purchases'],
        summary="Delete a Purchase Order",
        description="Deletes an unreceived purchase order. Orders that have already received goods cannot be deleted to preserve inventory audit history.",
        responses={
            204: OpenApiResponse(description="Purchase order successfully deleted."),
            400: OpenApiResponse(description="Cannot delete a purchase order that has already received goods."),
        }
    ),
    receive=extend_schema(
        tags=['Purchases'],
        summary="Receive Goods",
        description="Records actual quantities received from the supplier for specific line items. Increases physical stock in `Inventory`, records an immutable `InventoryLedgerEntry`, and transitions purchase status to `partially_received` or `received`.",
        request=PurchaseReceiveSerializer,
        examples=[_purchase_receive_example],
        responses={
            200: OpenApiResponse(
                response=PurchaseReceiveResponseSerializer,
                description="Goods successfully received, ledger recorded, and inventory updated."
            ),
            400: OpenApiResponse(description="Validation error (e.g. order cancelled, invalid item ID, or received quantity exceeds quantity ordered).")
        }
    )
)
