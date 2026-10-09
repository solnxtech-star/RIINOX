from drf_spectacular.utils import extend_schema_view, extend_schema, OpenApiExample, OpenApiResponse
from core.applications.sales.api.serializers import (
    SaleSerializer,
    SaleCreateSerializer,
    SaleReturnSerializer,
    SaleReturnCreateSerializer,
)

sale_viewset_schema = extend_schema_view(
    list=extend_schema(
        summary="List Sales",
        description="Retrieve a paginated list of all sales records.",
        tags=["Sales"],
        responses={200: OpenApiResponse(response=SaleSerializer(many=True), description="Successful retrieval of sales list")}
    ),
    retrieve=extend_schema(
        summary="Retrieve Sale",
        description="Retrieve detailed information about a specific sale by its ID.",
        tags=["Sales"],
        responses={200: OpenApiResponse(response=SaleSerializer, description="Successful retrieval of sale details")}
    ),
    create=extend_schema(
        summary="Create Sale",
        description="""Create a new sale record along with its line items. Triggers inventory update, ledger entry, and invoicing.

What happens in one transaction:

- the `Sale` and multiple `SaleItem` records are created;
- inventory quantities are deducted automatically across batches using FIFO via the `update_inventory_for_sale` service;
- an `InventoryLedgerEntry` is logged for every stock movement.

Where the values come from:

- `organization`: GET `/api/organizations/`
- `customer`: GET `/api/users/customers/`
- `location`: GET `/api/warehouse/warehouses/`
- `product` (in items): GET `/api/products/products/`
- `unit_name` (in items): Optional, must match one of the product's `unit_conversions` or base unit.

`items` must be an array of sale items. `quantity` is multiplied by the unit's `multiplier` behind the scenes.""",
        tags=["Sales"],
        request=SaleCreateSerializer,
        responses={201: OpenApiResponse(response=SaleSerializer, description="Sale created successfully")},
        examples=[
            OpenApiExample(
                "Example Sale Request",
                summary="Standard sale payload",
                description="Provides organization, customer, location, sales rep, and a list of sale items. Note that 'id', 'sale', and 'total' are not required in the items list during creation.",
                value={
                    "organization": 1,
                    "customer": 1,
                    "location": 1,
                    "sales_rep": 1,
                    "payment_status": "unpaid",
                    "fulfillment_status": "unfulfilled",
                    "currency": "USD",
                    "reference_number": "PO-12345",
                    "due_date": "2026-10-31",
                    "billing_address": "123 Business Rd",
                    "shipping_address": "456 Logistics Ave",
                    "subtotal": "500.00",
                    "discount_amount": "50.00",
                    "tax_amount": "22.50",
                    "notes": "Thank you for shopping with us.",
                    "items": [
                        {
                            "product": 1,
                            "variant": 1,
                            "product_name": "Pro Widget v2",
                            "product_sku": "WDG-PRO-2",
                            "quantity": 2,
                            "unit_price": "250.00",
                            "discount": "25.00",
                            "discount_type": "fixed",
                            "tax": "11.25",
                            "tax_rate": "5.00"
                        }
                    ]
                },
                request_only=True,
            )
        ]
    ),
    update=extend_schema(
        summary="Update Sale",
        description="Update an existing sale record entirely.",
        tags=["Sales"],
        request=SaleCreateSerializer,
        responses={200: OpenApiResponse(response=SaleSerializer, description="Sale updated successfully")}
    ),
    partial_update=extend_schema(
        summary="Partial Update Sale",
        description="Partially update an existing sale record.",
        tags=["Sales"],
        request=SaleCreateSerializer,
        responses={200: OpenApiResponse(response=SaleSerializer, description="Sale partially updated successfully")}
    ),
    destroy=extend_schema(
        summary="Delete Sale",
        description="Delete a sale record. Warning: this may orphan related ledger or invoice entries if not handled via service layer.",
        tags=["Sales"],
        responses={204: OpenApiResponse(description="Sale deleted successfully")}
    ),
)

sale_return_viewset_schema = extend_schema_view(
    list=extend_schema(
        summary="List Sale Returns",
        description="Retrieve a paginated list of all sale returns.",
        tags=["Sales"],
        responses={200: OpenApiResponse(response=SaleReturnSerializer(many=True), description="Successful retrieval of returns list")}
    ),
    retrieve=extend_schema(
        summary="Retrieve Sale Return",
        description="Retrieve detailed information about a specific sale return.",
        tags=["Sales"],
        responses={200: OpenApiResponse(response=SaleReturnSerializer, description="Successful retrieval of return details")}
    ),
    create=extend_schema(
        summary="Create Sale Return",
        description="""Create a new sale return against an original sale, specifying the returned items.

What happens in one transaction:

- the `SaleReturn` and `SaleReturnItem` records are created;
- returned stock is added back to the `Inventory` pool (if `restock_action="return_to_stock"`) via `update_inventory_for_return`;
- an `InventoryLedgerEntry` is recorded.

Where the values come from:

- `original_sale`: GET `/api/sales/sales/`
- `sale_item` (in items): Must be an ID from the original sale's items.
- `restock_action` (in items): Must be `return_to_stock` or `damage`.

`refund_amount` and `condition` are required fields for the return items.""",
        tags=["Sales"],
        request=SaleReturnCreateSerializer,
        responses={201: OpenApiResponse(response=SaleReturnSerializer, description="Sale return created successfully")},
        examples=[
            OpenApiExample(
                "Example Sale Return Request",
                summary="Standard return payload",
                description="Provides original_sale ID, organization, and the specific items being returned. 'sale_return' and 'id' should be omitted in the items array.",
                value={
                    "original_sale": 1,
                    "organization": 1,
                    "return_reason": "DEFECTIVE",
                    "refund_amount": "250.00",
                    "refund_method": "original_payment",
                    "items": [
                        {
                            "sale_item": 1,
                            "quantity": 1,
                            "refund_amount": "250.00",
                            "condition": "defective",
                            "restock_action": "return_to_stock"
                        }
                    ]
                },
                request_only=True,
            )
        ]
    ),
    update=extend_schema(
        summary="Update Sale Return",
        description="Update an existing sale return completely.",
        tags=["Sales"],
        request=SaleReturnCreateSerializer,
        responses={200: OpenApiResponse(response=SaleReturnSerializer, description="Sale return updated successfully")}
    ),
    partial_update=extend_schema(
        summary="Partial Update Sale Return",
        description="Partially update an existing sale return.",
        tags=["Sales"],
        request=SaleReturnCreateSerializer,
        responses={200: OpenApiResponse(response=SaleReturnSerializer, description="Sale return partially updated successfully")}
    ),
    destroy=extend_schema(
        summary="Delete Sale Return",
        description="Delete a sale return record.",
        tags=["Sales"],
        responses={204: OpenApiResponse(description="Sale return deleted successfully")}
    ),
)
