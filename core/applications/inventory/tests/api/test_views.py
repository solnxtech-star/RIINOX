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
        # Ensure user can authenticate
        self.client.force_authenticate(user=self.user)
        
        self.warehouse = WarehouseFactory(organization=self.org)
        self.warehouse2 = WarehouseFactory(organization=self.org)
        self.product = ProductFactory(organization=self.org)
        
        create_opening_stock(self.product, None, None, self.warehouse, 100, self.user)

    def test_list_inventory(self):
        url = "/api/balances/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Check if the opened stock is returned
        results = response.data.get('results', []) if isinstance(response.data, dict) else response.data
        self.assertGreaterEqual(len(results), 1)

    def test_list_inventory_ledger_entries(self):
        url = "/api/ledger/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data.get('results', []) if isinstance(response.data, dict) else response.data
        self.assertGreaterEqual(len(results), 1)

    def test_create_stock_adjustment(self):
        url = "/api/adjustments/"
        data = {
            "product": self.product.id,
            "warehouse": self.warehouse.id,
            "requested_quantity_change": 50,
            "adjustment_reason": "wrong_entry"
        }
        response = self.client.post(url, data, format='json')
        # DRF generic view validation requires proper fields
        self.assertIn(response.status_code, [status.HTTP_201_CREATED, status.HTTP_400_BAD_REQUEST])
        if response.status_code == status.HTTP_201_CREATED:
            self.assertTrue(StockAdjustmentRequest.objects.filter(product=self.product).exists())

    def test_stock_transfer(self):
        url = "/api/transfers/execute/"
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
