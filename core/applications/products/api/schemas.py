from drf_spectacular.utils import extend_schema, extend_schema_view, OpenApiExample

PRODUCT_CREATE_EXAMPLE = {
    "name": "Premium Wireless Headphones",
    "sku": "WH-1000XM5",
    "barcode": "4905524933923",
    "organization": 1,
    "category": 1,
    "brand": 1,
    "description": "Noise-cancelling over-ear headphones.",
    "quality_grade": "Grade A",
    "unit_of_measurement": "pcs",
    "product_type": "physical",
    "tax_config": "taxable",
    "tax_rate": "15.00",
    "track_inventory": True,
    "purchase_cost": "250.00",
    "vendor_sale_cost": "270.00",
    "customer_sale_price": "350.00",
    "minimum_stock_level": 10,
    "quantity_in_stock": 50,
    "warehouse_id": 1,
    "batches": [
        {
            "expiry_date": "2028-12-31",
            "cost_price": "245.00",
            "selling_price": "350.00"
        }
    ],
    "bulk_discounts": [
        {
            "min_quantity": 10,
            "discount_amount": "20.00"
        }
    ],
    "unit_conversions": [
        {
            "unit_name": "carton",
            "multiplier": "12.000",
            "price_override": "4000.00"
        }
    ]
}

product_schema = extend_schema_view(
    list=extend_schema(
        tags=['Products'],
        summary="List all Products",
        description="Retrieves a list of all products along with their nested batches, discounts, and unit conversions."
    ),
    create=extend_schema(
        tags=['Products'],
        summary="Create a New Product",
        description="""Creates a product, optionally including opening stock, nested batches, bulk discounts, and unit conversions.

What happens in one transaction:

- the `Product` is created;
- nested records (`ProductBatch`, `ProductBulkDiscount`, `ProductUnitConversion`) are created;
- if `quantity_in_stock` > 0 and `warehouse_id` is passed, `create_opening_stock` is called to create the initial `Inventory` and `InventoryLedgerEntry`.

**Understanding Opening Stock (`quantity_in_stock`):**
Opening stock (or beginning inventory) is the amount of physical stock you already have on hand at the exact moment you register this product in the system (e.g., migrating from an old software or spreadsheet).
- **Optional**: It is completely optional because you might be creating a product before physically having it in your warehouse (expecting to order it soon) or because it's a non-physical service.
- **Strict Ledger Rules**: If `track_inventory` is `True`, the system strictly enforces the inventory ledger. This means `current_stock` is read-only and is calculated entirely from ledger transactions (shipments, returns, sales). Setting `quantity_in_stock` here is your *only* chance to declare a starting balance for the ledger without executing an explicit receipt/adjustment transaction.

Where the values come from:

- `category`: GET `/api/products/categories/`
- `brand`: GET `/api/products/brands/`
- `warehouse_id`: GET `/api/warehouse/warehouses/` (optional, needed if `quantity_in_stock` > 0).

`track_inventory` is a boolean. If `True`, inventory ledger is strictly enforced. `quantity_in_stock` is optional. `current_stock` cannot be modified here.""",
        examples=[
            OpenApiExample(
                name="Complete Product Payload",
                value=PRODUCT_CREATE_EXAMPLE,
                request_only=True
            )
        ]
    ),
    retrieve=extend_schema(
        tags=['Products'],
        summary="Retrieve a Product",
        description="Fetch a specific product by its ID."
    ),
    update=extend_schema(
        tags=['Products'],
        summary="Update a Product",
        description="Fully update a product. Can also update nested relations.",
        examples=[
            OpenApiExample(
                name="Update Product Payload",
                value=PRODUCT_CREATE_EXAMPLE,
                request_only=True
            )
        ]
    ),
    partial_update=extend_schema(
        tags=['Products'],
        summary="Partially Update a Product",
        description="Partially update a product's fields."
    ),
    destroy=extend_schema(
        tags=['Products'],
        summary="Delete a Product",
        description="Removes a product from the database."
    ),
)
