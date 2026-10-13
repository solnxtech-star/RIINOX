from rest_framework.test import APITestCase
from rest_framework import status
from django.urls import reverse
from core.applications.inventory.models import StockAdjustmentRequest, Inventory, InventoryLedgerEntry
from core.applications.inventory.tests.factories import (
    OrganizationFactory, UserFactory, WarehouseFactory, ProductFactory
)
from core.applications.inventory.services import create_opening_stock

class InventoryAPITest(APITestCase):
    def setUp(self):
        self.org = OrganizationFactory()
        self.user = UserFactory(email="api@test.com")
        from core.applications.users.models import Membership
        from core.applications.users.services import seed_default_roles
        roles = seed_default_roles(self.org)
        Membership.objects.create(
            user=self.user,
            organization=self.org,
            role=roles["owner"],
            is_active=True,
        )
        # Ensure user can authenticate
        self.client.force_authenticate(user=self.user)
        
        self.warehouse = WarehouseFactory(organization=self.org)
        self.warehouse2 = WarehouseFactory(organization=self.org)
        self.product = ProductFactory(organization=self.org)
        
        create_opening_stock(self.product, None, None, self.warehouse, 100, self.user)

    @property
    def base_url(self):
        return f"/api/organizations/{self.org.id}/inventory"

    def test_list_inventory(self):
        url = f"{self.base_url}/balances/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Check if the opened stock is returned
        results = response.data.get('results', []) if isinstance(response.data, dict) else response.data
        self.assertGreaterEqual(len(results), 1)

    def test_list_inventory_ledger_entries(self):
        url = f"{self.base_url}/ledger/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data.get('results', []) if isinstance(response.data, dict) else response.data
        self.assertGreaterEqual(len(results), 1)

    def test_create_stock_adjustment(self):
        url = f"{self.base_url}/adjustments/"
        data = {
            "product": self.product.id,
            "warehouse": self.warehouse.id,
            "requested_quantity_change": 50,
            "adjustment_reason": "wrong_entry"
        }
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(StockAdjustmentRequest.objects.filter(product=self.product).exists())

    def test_stock_transfer(self):
        url = f"{self.base_url}/transfers/execute/"
        data = {
            "source_warehouse_id": self.warehouse.id,
            "destination_warehouse_id": self.warehouse2.id,
            "product_id": self.product.id,
            "quantity": 20
        }
        response = self.client.post(url, data, format='json')
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
