import uuid
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase
from core.applications.products.models import Product
from core.applications.inventory.tests.factories import OrganizationFactory, UserFactory

class ProductAPITest(APITestCase):
    def setUp(self):
        self.org = OrganizationFactory()
        self.user = UserFactory()
        self.client.force_authenticate(user=self.user)
        
        self.product = Product.objects.create(
            organization=self.org,
            name="Test Product 1",
            sku=f"SKU-{uuid.uuid4().hex[:5]}",
            customer_sale_price=10.0,
            purchase_cost=5.0
        )

    def test_list_products(self):
        url = "/api/products/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data.get('results', []) if isinstance(response.data, dict) else response.data
        self.assertGreaterEqual(len(results), 1)

    def test_create_product(self):
        url = "/api/products/"
        data = {
            "organization": self.org.id,
            "name": "New Product",
            "sku": f"SKU-{uuid.uuid4().hex[:5]}",
            "customer_sale_price": "20.00",
            "purchase_cost": "15.00"
        }
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["name"], "New Product")
        
        # Explicit DB check
        self.assertTrue(Product.objects.filter(sku=data["sku"]).exists())
        created_product = Product.objects.get(sku=data["sku"])
        self.assertEqual(created_product.name, "New Product")

    def test_retrieve_product(self):
        url = f"/api/products/{self.product.id}/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["id"].replace('-', ''), str(self.product.id).replace('-', ''))

    def test_update_product(self):
        url = f"/api/products/{self.product.id}/"
        data = {"name": "Updated Product Name"}
        response = self.client.patch(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["name"], "Updated Product Name")
        
        # Explicit DB check
        self.product.refresh_from_db()
        self.assertEqual(self.product.name, "Updated Product Name")

    def test_delete_product(self):
        url = f"/api/products/{self.product.id}/"
        response = self.client.delete(url)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Product.objects.filter(id=self.product.id).exists())
