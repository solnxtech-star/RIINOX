from drf_spectacular.utils import OpenApiExample

stock_adjustment_create_example = OpenApiExample(
    "Request Stock Adjustment",
    summary="Create a new stock adjustment request",
    description="""Requests a change in stock.

What happens in one transaction:

- a `StockAdjustmentRequest` is created with a status of `PENDING`;
- if the user is an Admin/Owner, it is manually or auto-approved later to modify stock and create an `InventoryLedgerEntry`.

Where the values come from:

- `product`: GET `/api/products/products/`
- `warehouse`: GET `/api/warehouse/warehouses/`
- `variant` and `batch`: Optional, specifies the exact inventory bucket to adjust.

`requested_quantity_change` is required and can be negative (shrinkage/loss) or positive (found stock).""",
    value={
        "product": "d290f1ee-6c54-4b01-90e6-d701748f0851",
        "variant": None,
        "batch": None,
        "warehouse": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
        "requested_quantity_change": -5,
        "reason": "damaged"
    },
    request_only=True
)

stock_transfer_example = OpenApiExample(
    "Transfer Stock",
    summary="Transfer stock between branches",
    description="""Atomically transfer stock from one warehouse to another.

What happens in one transaction:

- stock is deducted from `source_warehouse_id` `Inventory`;
- stock is added to `destination_warehouse_id` `Inventory`;
- `InventoryLedgerEntry` records are created for both locations to track the movement.

Where the values come from:

- `product_id`: GET `/api/products/products/`
- `source_warehouse_id` & `destination_warehouse_id`: GET `/api/warehouse/warehouses/`

`quantity` is required. `variant_id` and `batch_id` are optional to transfer specific variants/batches.""",
    value={
        "product_id": "d290f1ee-6c54-4b01-90e6-d701748f0851",
        "variant_id": None,
        "batch_id": None,
        "source_warehouse_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
        "destination_warehouse_id": "184d0b2b-6c58-47bc-9b63-1d01f11a8c88",
        "quantity": 50
    },
    request_only=True
)

from drf_spectacular.utils import extend_schema, extend_schema_view

inventory_schema = extend_schema_view(
    list=extend_schema(
        tags=['Inventory'],
        summary="List Inventory Balances",
        description="Retrieves current stock balances across all products and warehouses."
    ),
    retrieve=extend_schema(
        tags=['Inventory'],
        summary="Retrieve Specific Inventory Balance",
        description="Fetch a specific inventory record by ID."
    ),
)

ledger_entry_schema = extend_schema_view(
    list=extend_schema(
        tags=['Inventory'],
        summary="List Inventory Ledger Entries",
        description="Retrieves the immutable audit trail of all stock movements (adjustments, sales, transfers)."
    ),
    retrieve=extend_schema(
        tags=['Inventory'],
        summary="Retrieve Specific Ledger Entry",
        description="Fetch a specific ledger entry by ID."
    ),
)
