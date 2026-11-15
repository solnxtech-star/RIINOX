from decimal import Decimal

from rest_framework import status
from rest_framework.test import APITestCase

from core.applications.inventory.models import Inventory
from core.applications.inventory.models import StockAdjustmentRequest
from core.applications.inventory.services import create_opening_stock
from core.applications.inventory.tests.factories import OrganizationFactory
from core.applications.inventory.tests.factories import ProductFactory
from core.applications.inventory.tests.factories import UserFactory
from core.applications.inventory.tests.factories import WarehouseFactory
from core.applications.users.models import Membership
from core.applications.users.services import seed_default_roles


class InventoryAPITest(APITestCase):
    def setUp(self):
        self.org = OrganizationFactory()
        self.user = UserFactory(email="api@test.com")
        self.roles = seed_default_roles(self.org)
        Membership.objects.create(
            user=self.user,
            organization=self.org,
            role=self.roles["owner"],
            is_active=True,
        )
        # Ensure user can authenticate
        self.client.force_authenticate(user=self.user)

        self.warehouse = WarehouseFactory(organization=self.org, name="Main Warehouse")
        self.warehouse2 = WarehouseFactory(
            organization=self.org,
            name="Branch Warehouse",
        )
        self.product = ProductFactory(
            organization=self.org,
            purchase_cost=Decimal("100.00"),
            customer_sale_price=Decimal("150.00"),
            minimum_stock_level=20,
        )

        create_opening_stock(self.product, None, None, self.warehouse, 100, self.user)

    @property
    def base_url(self):
        return f"/api/organizations/{self.org.id}/inventory"

    def test_list_inventory(self):
        url = f"{self.base_url}/balances/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Check if the opened stock is returned
        results = (
            response.data.get("results", [])
            if isinstance(response.data, dict)
            else response.data
        )
        self.assertGreaterEqual(len(results), 1)

    def test_list_inventory_ledger_entries(self):
        url = f"{self.base_url}/ledger/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = (
            response.data.get("results", [])
            if isinstance(response.data, dict)
            else response.data
        )
        self.assertGreaterEqual(len(results), 1)

    def test_create_stock_adjustment(self):
        url = f"{self.base_url}/adjustments/"
        data = {
            "product": self.product.id,
            "warehouse": self.warehouse.id,
            "requested_quantity_change": 50,
            "adjustment_reason": "wrong_entry",
        }
        response = self.client.post(url, data, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(
            StockAdjustmentRequest.objects.filter(product=self.product).exists(),
        )

    def test_stock_transfer(self):
        url = f"{self.base_url}/transfers/execute/"
        data = {
            "source_warehouse_id": self.warehouse.id,
            "destination_warehouse_id": self.warehouse2.id,
            "product_id": self.product.id,
            "quantity": 20,
        }
        response = self.client.post(url, data, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        inv_src = Inventory.objects.get(product=self.product, warehouse=self.warehouse)
        inv_dst = Inventory.objects.get(product=self.product, warehouse=self.warehouse2)

        self.assertEqual(inv_src.quantity, 80)
        self.assertEqual(inv_dst.quantity, 20)

    def test_tenant_isolation_inventory(self):
        """User cannot access other organization's inventory endpoints -> 404 Not Found."""
        other_org = OrganizationFactory()
        resp = self.client.get(f"/api/organizations/{other_org.id}/inventory/balances/")
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_unauthenticated_inventory(self):
        """Unauthenticated user is rejected with 401."""
        self.client.logout()
        resp = self.client.get(f"{self.base_url}/balances/")
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_inventory_summary_organization_wide_and_warehouse_filtered(self):
        """
        Verifies summary figures on /inventory/balances/summary/, /inventory/summary/,
        and /products/summary/ both organization-wide and filtered by warehouse.
        """
        low_product = ProductFactory(
            organization=self.org,
            purchase_cost=Decimal("50.00"),
            customer_sale_price=Decimal("80.00"),
            minimum_stock_level=10,
        )
        create_opening_stock(low_product, None, None, self.warehouse2, 5, self.user)

        empty_product = ProductFactory(
            organization=self.org,
            purchase_cost=Decimal("200.00"),
            customer_sale_price=Decimal("300.00"),
            minimum_stock_level=5,
        )

        # 1. Organization-wide summary
        for summary_url in [
            f"{self.base_url}/balances/summary/",
            f"{self.base_url}/summary/",
            f"/api/organizations/{self.org.id}/products/summary/",
        ]:
            response = self.client.get(summary_url)
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            data = response.data
            # 100 * 100 + 5 * 50 = 10000 + 250 = 10250.00
            self.assertEqual(Decimal(data["stock_value"]), Decimal("10250.00"))
            # 100 * 150 + 5 * 80 = 15000 + 400 = 15400.00
            self.assertEqual(
                Decimal(data["potential_sales_value"]),
                Decimal("15400.00"),
            )
            # 15400 - 10250 = 5150.00
            self.assertEqual(Decimal(data["profit_to_be_made"]), Decimal("5150.00"))
            # low_product is low (5 <= 10), empty_product is out of stock (0) -> 2 requiring attention
            self.assertEqual(data["low_stock_count"], 1)
            self.assertEqual(data["out_of_stock_count"], 1)
            self.assertEqual(data["stock_requiring_attention"], 2)
            self.assertEqual(data["total_products"], 3)
            self.assertEqual(data["total_inventory_items"], 105)
            self.assertTrue(data["has_cost_permission"])

        # 2. Filtered by warehouse 1 (only self.product with 100 units)
        warehouse_response = self.client.get(
            f"/api/organizations/{self.org.id}/products/summary/?warehouse={self.warehouse.id}",
        )
        self.assertEqual(warehouse_response.status_code, status.HTTP_200_OK)
        wh_data = warehouse_response.data
        self.assertEqual(Decimal(wh_data["stock_value"]), Decimal("10000.00"))
        self.assertEqual(Decimal(wh_data["potential_sales_value"]), Decimal("15000.00"))
        self.assertEqual(Decimal(wh_data["profit_to_be_made"]), Decimal("5000.00"))
        self.assertEqual(wh_data["stock_requiring_attention"], 0)
        self.assertEqual(wh_data["total_inventory_items"], 100)
        self.assertEqual(
            str(wh_data["warehouse_id"]).replace("-", ""),
            str(self.warehouse.id).replace("-", ""),
        )
        self.assertEqual(wh_data["warehouse_name"], "Main Warehouse")

    def test_inventory_summary_cost_redaction_for_staff_without_cost_permission(self):
        """Inventory Staff has VIEW_INVENTORY and VIEW_PRODUCTS, but lacks VIEW_PRODUCT_COSTS."""
        staff_user = UserFactory(email="staff@test.com")
        Membership.objects.create(
            user=staff_user,
            organization=self.org,
            role=self.roles["inventory-staff"],
            is_active=True,
        )
        self.client.force_authenticate(user=staff_user)

        response = self.client.get(
            f"/api/organizations/{self.org.id}/products/summary/",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNone(response.data["stock_value"])
        self.assertIsNone(response.data["profit_to_be_made"])
        self.assertEqual(
            Decimal(response.data["potential_sales_value"]),
            Decimal("15000.00"),
        )
        self.assertFalse(response.data["has_cost_permission"])

    def test_inventory_summary_rejects_foreign_warehouse(self):
        """Passing a warehouse from another organization raises 400 ValidationError."""
        other_org = OrganizationFactory()
        foreign_warehouse = WarehouseFactory(organization=other_org)

        response = self.client.get(
            f"/api/organizations/{self.org.id}/products/summary/?warehouse={foreign_warehouse.id}",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        errors = response.data.get("errors", response.data)
        self.assertIn("warehouse", errors)

    def test_product_list_warehouse_filtering_and_scoped_current_stock(self):
        """
        Verifies GET /api/organizations/{organization_id}/products/?warehouse=<uuid>
        filters products to that warehouse and computes current_stock for that warehouse.
        """
        create_opening_stock(self.product, None, None, self.warehouse2, 30, self.user)
        branch_only_product = ProductFactory(
            organization=self.org,
            name="Branch Exclusive",
        )
        create_opening_stock(
            branch_only_product,
            None,
            None,
            self.warehouse2,
            15,
            self.user,
        )

        # Unfiltered: self.product has 100 + 30 = 130 total stock
        all_response = self.client.get(f"/api/organizations/{self.org.id}/products/")
        self.assertEqual(all_response.status_code, status.HTTP_200_OK)
        all_results = all_response.data.get("results", all_response.data)
        product_map = {
            str(item["id"]).replace("-", ""): item["current_stock"]
            for item in all_results
        }
        self.assertEqual(product_map[str(self.product.id).replace("-", "")], 130)
        self.assertEqual(product_map[str(branch_only_product.id).replace("-", "")], 15)

        # Filtered by Main Warehouse (warehouse 1): only self.product appears, with current_stock = 100
        wh1_response = self.client.get(
            f"/api/organizations/{self.org.id}/products/?warehouse={self.warehouse.id}",
        )
        self.assertEqual(wh1_response.status_code, status.HTTP_200_OK)
        wh1_results = wh1_response.data.get("results", wh1_response.data)
        wh1_map = {
            str(item["id"]).replace("-", ""): item["current_stock"]
            for item in wh1_results
        }
        self.assertEqual(len(wh1_map), 1)
        self.assertEqual(wh1_map[str(self.product.id).replace("-", "")], 100)
