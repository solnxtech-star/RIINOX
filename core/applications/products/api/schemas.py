from drf_spectacular.utils import OpenApiExample
from drf_spectacular.utils import OpenApiParameter
from drf_spectacular.utils import OpenApiResponse
from drf_spectacular.utils import OpenApiTypes
from drf_spectacular.utils import extend_schema
from drf_spectacular.utils import extend_schema_view

from core.applications.inventory.api.serializers import InventorySummarySerializer

PRODUCT_CREATE_EXAMPLE = {
    "name": "NEW TEST PRODUCT 1",
    "sku": "MED-AMX-500",
    "barcode": "8901234567890",
    "category": "b2c3d4e5-f6a7-4890-c1d2-e3f4a5b6c7d8",
    "brand": "c3d4e5f6-a7b8-4901-d2e3-f4a5b6c7d8e9",
    "description": "Broad-spectrum antibacterial medication.",
    "quality_grade": "Grade A",
    "unit_of_measurement": "box",
    "track_batches": True,
    "purchase_cost": "1200.00",
    "vendor_sale_cost": "1400.00",
    "customer_sale_price": "2000.00",
    "minimum_stock_level": 20,
    "quantity_in_stock": 50,
    "warehouse_id": "d4e5f6a7-b8c9-4012-e3f4-a5b6c7d8e9f0",
    "batches": [
        {
            "batch_number": "AMX-2026-B1",
            "expiry_date": "2027-12-31",
            "cost_price": "1200.00",
            "selling_price": "2000.00",
        },
    ],
    "bulk_discounts": [
        {
            "min_quantity": 10,
            "discount_amount": "100.00",
        },
    ],
    "unit_conversions": [
        {
            "unit_name": "carton",
            "multiplier": "24.000",
            "price_override": "45000.00",
        },
    ],
}

PRODUCT_ORGANIZATION_PARAM = OpenApiParameter(
    "organization_id",
    OpenApiTypes.UUID,
    OpenApiParameter.PATH,
    description="UUID of the tenant organization.",
)

product_schema = extend_schema_view(
    list=extend_schema(
        tags=["Products"],
        summary="List all Products",
        description="""Retrieves a paginated list of products for the active tenant, including nested batches, discounts, and packaging unit conversions.

### Multi-Tenancy & Tenant-Enforced URL Structure:
- **Tenant Path Isolation**: Endpoints are strictly scoped under `/api/organizations/{organization_id}/products/`.
- **Cross-Tenant Isolation**: Requesting products from an unauthorized tenant returns 404 Not Found.

### Field-Level Security & Cost Redaction (PRD §55):
- Users holding `VIEW_PRODUCT_COSTS` (Owner, Admin, Accountant) receive full monetary visibility (`purchase_cost`, `vendor_sale_cost`, and batch `cost_price`).
- Users without this permission (Sales Rep, Manager, Cashier) have cost fields dynamically stripped from the JSON response.

### Warehouse Filtering:
- Pass `?warehouse=<uuid>` to filter products stocked at a specific warehouse and dynamically compute `current_stock` for that warehouse.""",
        parameters=[
            PRODUCT_ORGANIZATION_PARAM,
            OpenApiParameter(
                "warehouse",
                OpenApiTypes.UUID,
                OpenApiParameter.QUERY,
                description="Filter products by warehouse UUID. When provided, `current_stock` reflects on-hand quantity in this warehouse.",
            ),
            OpenApiParameter(
                "status",
                OpenApiTypes.STR,
                OpenApiParameter.QUERY,
                description="Filter products by status ('active', 'draft', 'archived').",
            ),
            OpenApiParameter(
                "category",
                OpenApiTypes.UUID,
                OpenApiParameter.QUERY,
                description="Filter products by category UUID.",
            ),
            OpenApiParameter(
                "brand",
                OpenApiTypes.UUID,
                OpenApiParameter.QUERY,
                description="Filter products by brand UUID.",
            ),
            OpenApiParameter(
                "product_type",
                OpenApiTypes.STR,
                OpenApiParameter.QUERY,
                description="Filter products by product type ('standard', 'service', 'consumable').",
            ),
        ],
    ),
    summary=extend_schema(
        tags=["Products"],
        summary="Get Products & Inventory Summary Metrics",
        description="""Computes the 4 KPI summary cards for the Products and Inventory dashboard:
- **`stock_value`**: Total valuation of on-hand inventory at purchase cost (`Σ (quantity × purchase_cost)`).
- **`potential_sales_value`**: Total valuation of on-hand inventory at retail selling price (`Σ (quantity × customer_sale_price)`).
- **`profit_to_be_made`**: Projected gross profit (`potential_sales_value - stock_value`).
- **`stock_requiring_attention`**: Count of active tracked products that are low stock (`current_stock <= minimum_stock_level`) or out of stock (`current_stock == 0`).

### Warehouse Scoping:
- Omit `warehouse` to retrieve organization-wide metrics across all active branches.
- Pass `?warehouse=<uuid>` to scope all valuation and attention figures strictly to that warehouse.

### Cost Security (PRD §12, §27, §55):
- Requires `VIEW_PRODUCTS`.
- If the caller lacks `VIEW_PRODUCT_COSTS`, `stock_value` and `profit_to_be_made` are returned as `null` (`has_cost_permission: false`).""",
        parameters=[
            PRODUCT_ORGANIZATION_PARAM,
            OpenApiParameter(
                "warehouse",
                OpenApiTypes.UUID,
                OpenApiParameter.QUERY,
                description="Optional warehouse UUID to scope summary metrics to a single branch.",
            ),
        ],
        responses={
            200: OpenApiResponse(
                response=InventorySummarySerializer,
                description="Inventory and product valuation summary figures.",
                examples=[
                    OpenApiExample(
                        "Organization-Wide Inventory Summary",
                        value={
                            "stock_value": "1250000.00",
                            "potential_sales_value": "1850000.00",
                            "profit_to_be_made": "600000.00",
                            "stock_requiring_attention": 4,
                            "out_of_stock_count": 1,
                            "low_stock_count": 3,
                            "total_products": 45,
                            "total_inventory_items": 1420,
                            "warehouses_count": 2,
                            "warehouse_id": None,
                            "warehouse_name": "All Warehouses",
                            "has_cost_permission": True,
                        },
                        response_only=True,
                    ),
                ],
            ),
        },
    ),
    create=extend_schema(
        tags=["Products"],
        summary="Create a New Product",
        description="""Creates a product within the tenant organization, optionally provisioning starting inventory, nested batches, bulk discounts, and unit conversions.

### Atomic Creation & Inventory Provisioning:
1. Creates the base `Product` catalog record.
2. Creates nested child records (`ProductBatch`, `ProductBulkDiscount`, `ProductUnitConversion`).
3. If `quantity_in_stock > 0` and `warehouse_id` are provided, atomically calls `create_opening_stock`:
   - Creates a system `Transaction` (`transaction_type = 'opening_stock'`).
   - Inserts an immutable `InventoryLedgerEntry` (+`quantity_in_stock`).
   - Updates/creates the `Inventory` stock balance record.
   - Dispatches an audit event `INVENTORY_OPENING_STOCK_SET`.
4. Dispatches an audit event `PRODUCT_CREATED`.

### Permissions & Restrictions (PRD §28, §55):
- Requires `CREATE_PRODUCT`.
- Setting `purchase_cost` or `vendor_sale_cost` requires `EDIT_PRODUCT_COSTS`. Attempting to set costs without this permission returns HTTP 400 Bad Request.
- `category`, `brand`, and `warehouse_id` must belong to the specified `organization`. Cross-tenant relations return HTTP 400 Bad Request.

### Plan Limits & Subscription Invariants (PRD §10, §52):
- **Active Subscription**: Validates `ensure_subscription_active(organization)`. Inactive/expired subscriptions reject creation with `SUBSCRIPTION_REQUIRED` (HTTP 402/403).
- **Product Quota**: Atomically reserves a slot via `consume_usage(organization, UsageMetric.PRODUCTS)`. Exceeding plan limit returns `PLAN_LIMIT_REACHED` (HTTP 403). Archived products free up their quota slot.
- **Monthly Transactions**: If opening stock is provisioned, the initial transaction counts toward `max_transactions_per_month`.
- **Warehouse Quota**: The `warehouse_id` for opening stock must be active within `max_locations`.""",
        examples=[
            OpenApiExample(
                name="Complete Product Payload with Opening Stock",
                value=PRODUCT_CREATE_EXAMPLE,
                request_only=True,
            ),
        ],
    ),
    retrieve=extend_schema(
        tags=["Products"],
        summary="Retrieve a Product",
        description="""Fetch a single product by its UUID.

### Field-Level Security (PRD §55):
- `purchase_cost`, `vendor_sale_cost`, and nested `batches[].cost_price` are dynamically omitted if the caller lacks `VIEW_PRODUCT_COSTS`.

### Cross-Tenant Isolation:
- Requesting a product belonging to another tenant returns HTTP 404 Not Found (never 403, preventing resource enumeration).""",
    ),
    update=extend_schema(
        tags=["Products"],
        summary="Update a Product",
        description="""Fully update a product and synchronize child relations.

### Business Rules:
- Requires `EDIT_PRODUCT`.
- Modifying `customer_sale_price` requires `EDIT_SELLING_PRICE`.
- Modifying `purchase_cost` or `vendor_sale_cost` requires `EDIT_PRODUCT_COSTS`.
- Nested `bulk_discounts` and `unit_conversions` are fully replaced.
- Existing `batches` are updated by `batch_number` or newly created.
- Opening stock fields (`quantity_in_stock`, `warehouse_id`) are ignored on update.""",
        examples=[
            OpenApiExample(
                name="Update Product Payload",
                value=PRODUCT_CREATE_EXAMPLE,
                request_only=True,
            ),
        ],
    ),
    partial_update=extend_schema(
        tags=["Products"],
        summary="Partially Update a Product",
        description="""Partially update selected fields on a product (e.g. updating only `customer_sale_price` or `status`).

### Permissions:
- Changing price requires `EDIT_SELLING_PRICE`.
- Changing costs requires `EDIT_PRODUCT_COSTS`.""",
    ),
    destroy=extend_schema(
        tags=["Products"],
        summary="Delete or Archive a Product",
        description="""Deletes or archives a product based on inventory history invariants (PRD §44 & §60).

### Invariant Handling:
- **Clean Delete**: If the product has zero inventory ledger entries or sales transactions, it is hard-deleted from the database (HTTP 204 No Content).
- **Protected Archival**: If the product has existing stock movements or sales history, `ProtectedError` is trapped, the product is automatically soft-archived (`status = 'archived'`), an audit log `PRODUCT_ARCHIVED` is emitted, and HTTP 200 OK is returned with an explanatory message.""",
    ),
    archive=extend_schema(
        tags=["Products"],
        summary="Archive a Product",
        description="""Explicitly soft-archive an active product (PRD §44). Changes status to 'archived' and records audit event `product.archived`.""",
    ),
    restore=extend_schema(
        tags=["Products"],
        summary="Restore an Archived Product",
        description="""Restores a previously archived product back to active status (PRD §44). Evaluates subscription plan quota before activation and records audit event `product.restored`.""",
    ),
)


category_schema = extend_schema_view(
    list=extend_schema(
        tags=["Categories"],
        summary="List all Categories",
        description="Retrieves a list of categories scoped to the active tenant organization.",
        parameters=[PRODUCT_ORGANIZATION_PARAM],
    ),
    create=extend_schema(
        tags=["Categories"],
        summary="Create a Category",
        description="Creates a new product category within the active tenant organization.",
        parameters=[PRODUCT_ORGANIZATION_PARAM],
    ),
    retrieve=extend_schema(
        tags=["Categories"],
        summary="Retrieve a Category",
        description="Retrieves a specific category by its UUID.",
        parameters=[PRODUCT_ORGANIZATION_PARAM],
    ),
    update=extend_schema(
        tags=["Categories"],
        summary="Update a Category",
        description="Updates an existing category within the active tenant organization.",
        parameters=[PRODUCT_ORGANIZATION_PARAM],
    ),
    partial_update=extend_schema(
        tags=["Categories"],
        summary="Partially Update a Category",
        description="Partially updates an existing category within the active tenant organization.",
        parameters=[PRODUCT_ORGANIZATION_PARAM],
    ),
    destroy=extend_schema(
        tags=["Categories"],
        summary="Delete or Deactivate a Category",
        description="Deletes a category, or soft-deactivates it if referenced by existing products.",
        parameters=[PRODUCT_ORGANIZATION_PARAM],
    ),
)


brand_schema = extend_schema_view(
    list=extend_schema(
        tags=["Brands"],
        summary="List all Brands",
        description="Retrieves a list of brands scoped to the active tenant organization.",
        parameters=[PRODUCT_ORGANIZATION_PARAM],
    ),
    create=extend_schema(
        tags=["Brands"],
        summary="Create a Brand",
        description="Creates a new brand within the active tenant organization.",
        parameters=[PRODUCT_ORGANIZATION_PARAM],
    ),
    retrieve=extend_schema(
        tags=["Brands"],
        summary="Retrieve a Brand",
        description="Retrieves a specific brand by its UUID.",
        parameters=[PRODUCT_ORGANIZATION_PARAM],
    ),
    update=extend_schema(
        tags=["Brands"],
        summary="Update a Brand",
        description="Updates an existing brand within the active tenant organization.",
        parameters=[PRODUCT_ORGANIZATION_PARAM],
    ),
    partial_update=extend_schema(
        tags=["Brands"],
        summary="Partially Update a Brand",
        description="Partially updates an existing brand within the active tenant organization.",
        parameters=[PRODUCT_ORGANIZATION_PARAM],
    ),
    destroy=extend_schema(
        tags=["Brands"],
        summary="Delete or Deactivate a Brand",
        description="Deletes a brand, or soft-deactivates it if referenced by existing products.",
        parameters=[PRODUCT_ORGANIZATION_PARAM],
    ),
)
