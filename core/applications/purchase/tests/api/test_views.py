from rest_framework.test import APITestCase
from rest_framework import status
from core.applications.purchase.models import Purchase, PurchaseItem
from core.applications.vendors.models import Vendor
from core.applications.inventory.tests.factories import OrganizationFactory, UserFactory, WarehouseFactory, ProductFactory

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
            discount=1000
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

    def test_create_purchase(self):
        """Simulate a user creating a new Purchase Order."""
        url = "/api/purchases/"
        data = {
            "organization": str(self.org.id),
            "vendor": str(self.vendor.id),
            "warehouse": str(self.warehouse.id),
            "delivery_method": "Supplier Delivery",
            "other_costs": 5000.00,
            "discount": 0.00,
        }
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIsNotNone(response.data['id'])
        # Verify it's a UUID
        self.assertTrue(isinstance(response.data['id'], str))

    def test_receive_purchase_integration(self):
        """
        Complete integration test simulating a user receiving goods against a purchase order.
        Verifies that hitting the endpoint updates the backend state using UUIDs.
        """
        url = f"/api/purchases/{self.purchase.id}/receive/"
        # Ensure we pass item_id as a string UUID to match frontend payload behavior
        item_uuid = str(self.purchase_item.id)
        
        data = {
            "items_received": [
                {
                    "item_id": item_uuid,
                    "quantity_received": 50
                }
            ]
        }
        
        # Real user hits the receive endpoint
        response = self.client.post(url, data, format='json')
        
        # Verify success
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        # Verify backend state updated correctly
        self.purchase_item.refresh_from_db()
        self.assertEqual(self.purchase_item.quantity_received, 50)
        
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
        self.assertIn('items_received', response.data.get('errors', response.data))
