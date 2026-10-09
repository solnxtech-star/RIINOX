import uuid
from rest_framework.test import APITestCase
from rest_framework import status
from core.applications.purchase.models import Purchase, PurchaseItem
from core.applications.vendors.models import Vendor
from core.applications.inventory.tests.factories import OrganizationFactory, UserFactory, WarehouseFactory, ProductFactory
from core.helper.enums import PurchaseStatusChoices


class PurchaseAPITest(APITestCase):
    def setUp(self):
        self.org = OrganizationFactory()
        self.user = UserFactory()
        self.client.force_authenticate(user=self.user)
        self.warehouse = WarehouseFactory(organization=self.org)
        self.product = ProductFactory(organization=self.org)
        self.vendor = Vendor.objects.create(organization=self.org, company_name="Test Vendor")

        self.purchase = Purchase.objects.create(
            organization=self.org,
            vendor=self.vendor,
            warehouse=self.warehouse,
            ordered_by=self.user,
            other_costs=5000,
            discount=1000,
        )
        self.purchase_item = PurchaseItem.objects.create(
            purchase=self.purchase,
            product=self.product,
            quantity_ordered=100,
            purchase_cost=10.0,
        )

    def test_list_purchases(self):
        """Simulate real user fetching their purchase order list."""
        url = "/api/purchases/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data.get('results', []) if isinstance(response.data, dict) else response.data
        self.assertGreaterEqual(len(results), 1)
        self.assertEqual(str(results[0]['id']).replace('-', ''), str(self.purchase.id).replace('-', ''))

    def test_create_purchase_with_items_round_trip(self):
        """
        Verify creating a Purchase Order WITH nested items (per our serializer standard).
        Performs Round-Trip check: POST -> verify DB -> GET detail endpoint.
        """
        url = "/api/purchases/"
        product2 = ProductFactory(organization=self.org)
        data = {
            "organization": str(self.org.id),
            "vendor": str(self.vendor.id),
            "warehouse": str(self.warehouse.id),
            "delivery_method": "Supplier Delivery",
            "other_costs": "2000.00",
            "discount": "500.00",
            "items": [
                {
                    "product": str(self.product.id),
                    "quantity_ordered": 50,
                    "purchase_cost": "15.00",
                    "discount": "0.00"
                },
                {
                    "product": str(product2.id),
                    "quantity_ordered": 20,
                    "purchase_cost": "30.00",
                    "discount": "0.00"
                }
            ]
        }
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        purchase_id = response.data['id']

        # 1. Database state verification (Guards against silent drops)
        created_purchase = Purchase.objects.get(id=purchase_id)
        self.assertEqual(created_purchase.items.count(), 2)
        item1 = created_purchase.items.get(product=self.product)
        self.assertEqual(item1.quantity_ordered, 50)
        self.assertEqual(item1.purchase_cost, 15.00)

        # 2. Round-Trip GET verification (Read Serializer checks)
        get_response = self.client.get(f"/api/purchases/{purchase_id}/")
        self.assertEqual(get_response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(get_response.data['items']), 2)
        self.assertEqual(get_response.data['status'], PurchaseStatusChoices.DRAFT)

    def test_receive_purchase_transitions_status(self):
        """
        Integration test verifying goods receipt:
        - Updates PurchaseItem.quantity_received
        - Transitions Purchase.status from draft -> partially_received -> received
        """
        url = f"/api/purchases/{self.purchase.id}/receive/"
        item_uuid = str(self.purchase_item.id)

        # Receive 50 units out of 100
        data = {
            "items_received": [
                {
                    "item_id": item_uuid,
                    "quantity_received": 50
                }
            ]
        }
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["purchase_status"], PurchaseStatusChoices.PARTIALLY_RECEIVED)

        self.purchase.refresh_from_db()
        self.assertEqual(self.purchase.status, PurchaseStatusChoices.PARTIALLY_RECEIVED)
        self.assertEqual(self.purchase.received_by, self.user)

        # Receive remaining 50 units
        response2 = self.client.post(url, data, format='json')
        self.assertEqual(response2.status_code, status.HTTP_200_OK)
        self.assertEqual(response2.data["purchase_status"], PurchaseStatusChoices.RECEIVED)

        self.purchase.refresh_from_db()
        self.assertEqual(self.purchase.status, PurchaseStatusChoices.RECEIVED)

    def test_receive_purchase_over_receiving_rejected(self):
        """Over-receiving goods beyond quantity_ordered must fail with HTTP 400."""
        url = f"/api/purchases/{self.purchase.id}/receive/"
        data = {
            "items_received": [
                {
                    "item_id": str(self.purchase_item.id),
                    "quantity_received": 150  # ordered 100
                }
            ]
        }
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["code"], "RECEIVE_ERROR")

    def test_cannot_delete_received_purchase(self):
        """Orders with received items cannot be hard-deleted (PRD §18)."""
        # Receive goods first
        self.purchase_item.quantity_received = 10
        self.purchase_item.save(update_fields=['quantity_received'])

        url = f"/api/purchases/{self.purchase.id}/"
        response = self.client.delete(url)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["code"], "CANNOT_DELETE_RECEIVED_PURCHASE")
        self.assertTrue(Purchase.objects.filter(id=self.purchase.id).exists())

    def test_receive_purchase_invalid_uuid(self):
        """Test rejection when passing an invalid UUID string."""
        url = f"/api/purchases/{self.purchase.id}/receive/"
        data = {
            "items_received": [
                {
                    "item_id": "not-a-valid-uuid-1234",
                    "quantity_received": 50
                }
            ]
        }
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
