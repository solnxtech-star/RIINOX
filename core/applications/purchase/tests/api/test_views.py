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
        )
        self.purchase_item = PurchaseItem.objects.create(
            purchase=self.purchase,
            product=self.product,
            quantity_ordered=100,
            unit_cost=10.0,
        )

    def test_list_purchases(self):
        url = "/api/purchases/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data.get('results', []) if isinstance(response.data, dict) else response.data
        self.assertGreaterEqual(len(results), 1)

    def test_receive_purchase(self):
        url = f"/api/purchases/{self.purchase.id}/receive/"
        data = {
            "items_received": [
                {
                    "item_id": self.purchase_item.id,
                    "quantity_received": 50
                }
            ]
        }
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.purchase_item.refresh_from_db()
        self.assertEqual(self.purchase_item.quantity_received, 50)
