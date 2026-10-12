import uuid
from decimal import Decimal
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase
from django.core.exceptions import ValidationError
from core.applications.products.models import Brand, Category, Product, ProductBatch
from core.applications.users.models import Membership
from core.applications.users.services import seed_default_roles
from core.applications.inventory.tests.factories import OrganizationFactory, UserFactory
from core.applications.warehouse.models import Warehouse

EXPECTED_PRODUCT_FIELDS = [
    'id', 'visible', 'created_at', 'updated_at', 'name', 'sku', 'barcode',
    'description', 'quality_grade', 'unit_of_measurement', 'product_type',
    'tax_config', 'tax_rate', 'track_inventory', 'track_batches',
    'purchase_cost', 'vendor_sale_cost', 'customer_sale_price',
    'minimum_stock_level', 'low_stock_alert_unit', 'opening_stock',
    'status', 'organization', 'category', 'brand', 'created_by',
    'current_stock', 'batches', 'bulk_discounts', 'unit_conversions'
]

EXPECTED_PRODUCT_FIELDS_NO_COSTS = [
    field for field in EXPECTED_PRODUCT_FIELDS if field not in ('purchase_cost', 'vendor_sale_cost')
]


class ProductAPITest(APITestCase):
    def setUp(self):
        # 1. Primary Organization setup
        self.org = OrganizationFactory()
        self.roles = seed_default_roles(self.org)

        self.owner = UserFactory()
        self.owner_membership = Membership.objects.create(
            user=self.owner,
            organization=self.org,
            role=self.roles["owner"],
            is_active=True,
        )

        self.manager = UserFactory()
        self.manager_membership = Membership.objects.create(
            user=self.manager,
            organization=self.org,
            role=self.roles["manager"],
            is_active=True,
        )

        self.sales_rep = UserFactory()
        self.sales_rep_membership = Membership.objects.create(
            user=self.sales_rep,
            organization=self.org,
            role=self.roles["sales-representative"],
            is_active=True,
        )

        # 2. Secondary Organization setup for Tenant Boundary verification
        self.other_org = OrganizationFactory()
        self.other_roles = seed_default_roles(self.other_org)

        self.other_user = UserFactory()
        self.other_membership = Membership.objects.create(
            user=self.other_user,
            organization=self.other_org,
            role=self.other_roles["owner"],
            is_active=True,
        )

        # 3. Base Products
        self.product = Product.objects.create(
            organization=self.org,
            name="Org1 Product",
            sku=f"SKU-{uuid.uuid4().hex[:6]}",
            customer_sale_price=Decimal("25.00"),
            purchase_cost=Decimal("15.00"),
            created_by=self.owner,
        )

        self.other_product = Product.objects.create(
            organization=self.other_org,
            name="Org2 Product",
            sku=f"SKU-OTHER-{uuid.uuid4().hex[:6]}",
            customer_sale_price=Decimal("40.00"),
            purchase_cost=Decimal("20.00"),
            created_by=self.other_user,
        )

        # Authenticate default client as owner
        self.client.force_authenticate(user=self.owner)

    # -------------------------------------------------------------------------
    # Owner / Admin Full Lifecycle Tests
    # -------------------------------------------------------------------------
    def test_owner_list_products_scoped_and_shape(self):
        """Owner can list products; response includes full schema and is scoped to their tenant."""
        url = "/api/products/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        results = response.data.get("results", response.data)
        product_ids = [r["id"].replace("-", "") for r in results]
        
        # Scoped to self.org only
        self.assertIn(str(self.product.id).replace("-", ""), product_ids)
        self.assertNotIn(str(self.other_product.id).replace("-", ""), product_ids)

        # Strict shape assertion
        first_item = results[0]
        self.assertCountEqual(first_item.keys(), EXPECTED_PRODUCT_FIELDS)
        self.assertEqual(first_item["purchase_cost"], "15.00")

    def test_owner_create_product_round_trip(self):
        """Owner creates product with nested batches; verify response shape, DB state, and GET round-trip."""
        url = "/api/products/"
        sku = f"SKU-{uuid.uuid4().hex[:6]}"
        data = {
            "organization": str(self.org.id),
            "name": "Owner Created Product",
            "sku": sku,
            "customer_sale_price": "50.00",
            "purchase_cost": "30.00",
            "batches": [
                {
                    "batch_number": "BATCH-OWNER-01",
                    "cost_price": "28.00",
                    "selling_price": "50.00"
                }
            ]
        }
        post_resp = self.client.post(url, data, format="json")
        self.assertEqual(post_resp.status_code, status.HTTP_201_CREATED)
        created_id = post_resp.data["id"]

        # DB verification
        self.assertTrue(Product.objects.filter(sku=sku).exists())
        created_db_prod = Product.objects.get(sku=sku)
        self.assertEqual(created_db_prod.name, "Owner Created Product")
        self.assertEqual(created_db_prod.batches.count(), 1)
        self.assertEqual(created_db_prod.batches.first().batch_number, "BATCH-OWNER-01")

        # Round-Trip Test (POST -> GET)
        get_url = f"/api/products/{created_id}/"
        get_resp = self.client.get(get_url)
        self.assertEqual(get_resp.status_code, status.HTTP_200_OK)
        self.assertCountEqual(get_resp.data.keys(), EXPECTED_PRODUCT_FIELDS)
        self.assertEqual(get_resp.data["purchase_cost"], "30.00")
        self.assertEqual(len(get_resp.data["batches"]), 1)
        self.assertEqual(get_resp.data["batches"][0]["cost_price"], "28.00")

    def test_owner_retrieve_product(self):
        """Owner retrieves product; all cost fields are returned."""
        url = f"/api/products/{self.product.id}/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertCountEqual(response.data.keys(), EXPECTED_PRODUCT_FIELDS)
        self.assertEqual(response.data["name"], self.product.name)
        self.assertEqual(response.data["purchase_cost"], "15.00")

    def test_owner_update_product(self):
        """Owner can update product attributes and price."""
        url = f"/api/products/{self.product.id}/"
        data = {
            "name": "Updated Org1 Product",
            "customer_sale_price": "35.00"
        }
        response = self.client.patch(url, data, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.product.refresh_from_db()
        self.assertEqual(self.product.name, "Updated Org1 Product")
        self.assertEqual(self.product.customer_sale_price, Decimal("35.00"))

    def test_owner_delete_product(self):
        """Owner can archive/delete a product."""
        url = f"/api/products/{self.product.id}/"
        response = self.client.delete(url)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Product.objects.filter(id=self.product.id).exists())

    # -------------------------------------------------------------------------
    # Sales Representative Permission & Field Redaction Tests
    # -------------------------------------------------------------------------
    def test_sales_rep_list_and_retrieve_masks_costs(self):
        """Sales Rep has VIEW_PRODUCTS but not VIEW_PRODUCT_COSTS: costs are stripped from JSON."""
        self.client.force_authenticate(user=self.sales_rep)

        # List
        list_resp = self.client.get("/api/products/")
        self.assertEqual(list_resp.status_code, status.HTTP_200_OK)
        results = list_resp.data.get("results", list_resp.data)
        self.assertGreaterEqual(len(results), 1)
        first_item = results[0]
        self.assertCountEqual(first_item.keys(), EXPECTED_PRODUCT_FIELDS_NO_COSTS)
        self.assertNotIn("purchase_cost", first_item)
        self.assertNotIn("vendor_sale_cost", first_item)

        # Retrieve
        get_resp = self.client.get(f"/api/products/{self.product.id}/")
        self.assertEqual(get_resp.status_code, status.HTTP_200_OK)
        self.assertCountEqual(get_resp.data.keys(), EXPECTED_PRODUCT_FIELDS_NO_COSTS)
        self.assertNotIn("purchase_cost", get_resp.data)
        self.assertNotIn("vendor_sale_cost", get_resp.data)

    def test_sales_rep_cannot_create_update_or_delete_product(self):
        """Sales Rep lacks CREATE_PRODUCT, EDIT_PRODUCT, ARCHIVE_PRODUCT -> 403 Forbidden."""
        self.client.force_authenticate(user=self.sales_rep)

        # Attempt Create
        post_resp = self.client.post("/api/products/", {
            "organization": str(self.org.id),
            "name": "Unauthorized Product",
            "sku": "SKU-UNAUTH-01",
            "customer_sale_price": "20.00"
        }, format="json")
        self.assertEqual(post_resp.status_code, status.HTTP_403_FORBIDDEN)

        # Attempt Update
        patch_resp = self.client.patch(f"/api/products/{self.product.id}/", {
            "name": "Tampered Name"
        }, format="json")
        self.assertEqual(patch_resp.status_code, status.HTTP_403_FORBIDDEN)

        # Attempt Delete
        delete_resp = self.client.delete(f"/api/products/{self.product.id}/")
        self.assertEqual(delete_resp.status_code, status.HTTP_403_FORBIDDEN)

    # -------------------------------------------------------------------------
    # Manager Permissions & Cost Protection Tests
    # -------------------------------------------------------------------------
    def test_manager_cannot_set_or_edit_purchase_costs(self):
        """Manager has CREATE_PRODUCT but lacks EDIT_PRODUCT_COSTS: setting purchase_cost returns 400."""
        self.client.force_authenticate(user=self.manager)

        # Attempt Create with purchase_cost
        post_resp = self.client.post("/api/products/", {
            "organization": str(self.org.id),
            "name": "Manager Product With Cost",
            "sku": f"SKU-{uuid.uuid4().hex[:6]}",
            "customer_sale_price": "45.00",
            "purchase_cost": "25.00"
        }, format="json")
        self.assertEqual(post_resp.status_code, status.HTTP_400_BAD_REQUEST)
        errors = post_resp.data.get("errors", post_resp.data)
        self.assertIn("purchase_cost", errors)

        # Attempt Patch with purchase_cost
        patch_resp = self.client.patch(f"/api/products/{self.product.id}/", {
            "purchase_cost": "20.00"
        }, format="json")
        self.assertEqual(patch_resp.status_code, status.HTTP_400_BAD_REQUEST)
        patch_errors = patch_resp.data.get("errors", patch_resp.data)
        self.assertIn("purchase_cost", patch_errors)

    def test_manager_can_create_and_edit_without_costs(self):
        """Manager can create product with selling price and update prices (has EDIT_SELLING_PRICE)."""
        self.client.force_authenticate(user=self.manager)

        sku = f"SKU-{uuid.uuid4().hex[:6]}"
        post_resp = self.client.post("/api/products/", {
            "organization": str(self.org.id),
            "name": "Manager Legal Product",
            "sku": sku,
            "customer_sale_price": "45.00"
        }, format="json")
        self.assertEqual(post_resp.status_code, status.HTTP_201_CREATED)
        created_id = post_resp.data["id"]

        # Follow-up GET: costs are hidden from manager
        get_resp = self.client.get(f"/api/products/{created_id}/")
        self.assertEqual(get_resp.status_code, status.HTTP_200_OK)
        self.assertNotIn("purchase_cost", get_resp.data)

        # Manager can update selling price
        patch_resp = self.client.patch(f"/api/products/{self.product.id}/", {
            "customer_sale_price": "29.99"
        }, format="json")
        self.assertEqual(patch_resp.status_code, status.HTTP_200_OK)
        self.product.refresh_from_db()
        self.assertEqual(self.product.customer_sale_price, Decimal("29.99"))

    # -------------------------------------------------------------------------
    # Multi-Tenant Boundary Isolation Tests
    # -------------------------------------------------------------------------
    def test_tenant_isolation_cannot_access_other_organization_product(self):
        """User from Org 2 cannot retrieve, update, delete, or list Org 1 products."""
        self.client.force_authenticate(user=self.other_user)

        # List must not include Org 1 product
        list_resp = self.client.get("/api/products/")
        self.assertEqual(list_resp.status_code, status.HTTP_200_OK)
        results = list_resp.data.get("results", list_resp.data)
        product_ids = [r["id"].replace("-", "") for r in results]
        self.assertNotIn(str(self.product.id).replace("-", ""), product_ids)
        self.assertIn(str(self.other_product.id).replace("-", ""), product_ids)

        # Retrieve Org 1 product -> 404 Not Found
        get_resp = self.client.get(f"/api/products/{self.product.id}/")
        self.assertEqual(get_resp.status_code, status.HTTP_404_NOT_FOUND)

        # Update Org 1 product -> 404 Not Found
        patch_resp = self.client.patch(f"/api/products/{self.product.id}/", {"name": "Hacked"}, format="json")
        self.assertEqual(patch_resp.status_code, status.HTTP_404_NOT_FOUND)

        # Delete Org 1 product -> 404 Not Found
        del_resp = self.client.delete(f"/api/products/{self.product.id}/")
        self.assertEqual(del_resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_tenant_isolation_cannot_create_product_in_unauthorized_org(self):
        """User from Org 2 cannot create a product specifying Org 1 ID -> 403 Forbidden."""
        self.client.force_authenticate(user=self.other_user)

        post_resp = self.client.post("/api/products/", {
            "organization": str(self.org.id),
            "name": "Cross Tenant Intrusion",
            "sku": "SKU-CROSS-01",
            "customer_sale_price": "10.00"
        }, format="json")
        self.assertEqual(post_resp.status_code, status.HTTP_403_FORBIDDEN)

    # -------------------------------------------------------------------------
    # Negative Validation Tests
    # -------------------------------------------------------------------------
    def test_negative_validation_missing_required_fields(self):
        """Missing required fields (name, sku, organization) returns 400 Bad Request."""
        response = self.client.post("/api/products/", {}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        errors = response.data.get("errors", response.data)
        self.assertIn("name", errors)
        self.assertIn("sku", errors)
        self.assertIn("organization", errors)

    # -------------------------------------------------------------------------
    # Cross-Tenant Category, Brand & Warehouse Validation Tests
    # -------------------------------------------------------------------------
    def test_cannot_create_product_with_other_org_category(self):
        """Cannot assign a Category belonging to another organization -> 400 Bad Request."""
        other_cat = Category.objects.create(organization=self.other_org, name="Foreign Category")
        response = self.client.post("/api/products/", {
            "organization": str(self.org.id),
            "name": "Invalid Category Product",
            "sku": f"SKU-{uuid.uuid4().hex[:6]}",
            "customer_sale_price": "20.00",
            "category": str(other_cat.id),
        }, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        errors = response.data.get("errors", response.data)
        self.assertIn("category", errors)
        self.assertIn("different organization", str(errors["category"]))

    def test_cannot_create_product_with_other_org_brand(self):
        """Cannot assign a Brand belonging to another organization -> 400 Bad Request."""
        other_brand = Brand.objects.create(organization=self.other_org, name="Foreign Brand")
        response = self.client.post("/api/products/", {
            "organization": str(self.org.id),
            "name": "Invalid Brand Product",
            "sku": f"SKU-{uuid.uuid4().hex[:6]}",
            "customer_sale_price": "20.00",
            "brand": str(other_brand.id),
        }, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        errors = response.data.get("errors", response.data)
        self.assertIn("brand", errors)
        self.assertIn("different organization", str(errors["brand"]))

    def test_cannot_create_product_with_other_org_warehouse(self):
        """Cannot specify opening stock in another organization's Warehouse -> 400 Bad Request."""
        other_warehouse = Warehouse.objects.create(
            organization=self.other_org,
            name="Foreign Warehouse",
            code=f"WH-{uuid.uuid4().hex[:4]}",
        )
        response = self.client.post("/api/products/", {
            "organization": str(self.org.id),
            "name": "Invalid Warehouse Product",
            "sku": f"SKU-{uuid.uuid4().hex[:6]}",
            "customer_sale_price": "20.00",
            "warehouse_id": str(other_warehouse.id),
            "quantity_in_stock": 5,
        }, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        errors = response.data.get("errors", response.data)
        self.assertIn("warehouse_id", errors)
        self.assertIn("different organization", str(errors["warehouse_id"]))

    def test_can_create_product_with_own_org_category_and_brand(self):
        """Can assign Category and Brand belonging to the same organization -> 201 Created."""
        cat = Category.objects.create(organization=self.org, name="My Org Category")
        brand = Brand.objects.create(organization=self.org, name="My Org Brand")
        sku = f"SKU-{uuid.uuid4().hex[:6]}"
        response = self.client.post("/api/products/", {
            "organization": str(self.org.id),
            "name": "Valid Scoped Product",
            "sku": sku,
            "customer_sale_price": "25.00",
            "category": str(cat.id),
            "brand": str(brand.id),
        }, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        product = Product.objects.get(sku=sku)
        self.assertEqual(product.category_id, uuid.UUID(str(cat.id)))
        self.assertEqual(product.brand_id, uuid.UUID(str(brand.id)))

    def test_cannot_update_product_with_other_org_category_or_brand(self):
        """Cannot patch an existing product with another organization's Category or Brand -> 400 Bad Request."""
        other_cat = Category.objects.create(organization=self.other_org, name="Foreign Cat 2")
        patch_resp = self.client.patch(
            f"/api/products/{self.product.id}/",
            {"category": str(other_cat.id)},
            format="json",
        )
        self.assertEqual(patch_resp.status_code, status.HTTP_400_BAD_REQUEST)
        errors = patch_resp.data.get("errors", patch_resp.data)
        self.assertIn("category", errors)

        other_brand = Brand.objects.create(organization=self.other_org, name="Foreign Brand 2")
        patch_resp = self.client.patch(
            f"/api/products/{self.product.id}/",
            {"brand": str(other_brand.id)},
            format="json",
        )
        self.assertEqual(patch_resp.status_code, status.HTTP_400_BAD_REQUEST)
        errors = patch_resp.data.get("errors", patch_resp.data)
        self.assertIn("brand", errors)


    def test_model_clean_enforces_category_and_brand_tenant_scope(self):
        """Model-level clean() directly raises ValidationError for foreign Category or Brand."""
        other_cat = Category.objects.create(organization=self.other_org, name="Foreign Clean Cat")
        other_brand = Brand.objects.create(organization=self.other_org, name="Foreign Clean Brand")

        prod_invalid_cat = Product(
            organization=self.org,
            name="Model Clean Cat Test",
            sku=f"SKU-{uuid.uuid4().hex[:6]}",
            category=other_cat,
        )
        with self.assertRaises(ValidationError) as cm:
            prod_invalid_cat.clean()
        self.assertIn("category", cm.exception.message_dict)

        prod_invalid_brand = Product(
            organization=self.org,
            name="Model Clean Brand Test",
            sku=f"SKU-{uuid.uuid4().hex[:6]}",
            brand=other_brand,
        )
        with self.assertRaises(ValidationError) as cm:
            prod_invalid_brand.clean()
        self.assertIn("brand", cm.exception.message_dict)

    def test_list_products_scopes_to_current_user_organization_by_default(self):
        """
        When a user belongs to multiple organizations, GET /api/products/
        without ?organization scopes by default to their current (primary) organization.
        Supplying ?organization=<org_id> explicitly switches scope to that tenant.
        """
        # Add owner as an active member of other_org as well
        Membership.objects.create(
            user=self.owner,
            organization=self.other_org,
            role=self.other_roles["owner"],
            is_active=True,
        )

        url = "/api/products/"
        # 1. Without organization parameter -> scopes to self.owner.organization (self.org)
        resp_default = self.client.get(url)
        self.assertEqual(resp_default.status_code, status.HTTP_200_OK)
        results_default = resp_default.data.get("results", resp_default.data)
        ids_default = [r["id"].replace("-", "") for r in results_default]
        self.assertIn(str(self.product.id).replace("-", ""), ids_default)
        self.assertNotIn(str(self.other_product.id).replace("-", ""), ids_default)

        # 2. With explicit query param ?organization=<other_org.id> -> scopes to other_org
        resp_other = self.client.get(f"{url}?organization={self.other_org.id}")
        self.assertEqual(resp_other.status_code, status.HTTP_200_OK)
        results_other = resp_other.data.get("results", resp_other.data)
        ids_other = [r["id"].replace("-", "") for r in results_other]
        self.assertNotIn(str(self.product.id).replace("-", ""), ids_other)
        self.assertIn(str(self.other_product.id).replace("-", ""), ids_other)


