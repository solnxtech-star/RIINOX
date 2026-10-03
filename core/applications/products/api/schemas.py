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
            "unit_name": "Carton",
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
        description="Creates a new product. Can optionally include initial opening stock, nested batches, bulk discounts, and unit conversions.",
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
