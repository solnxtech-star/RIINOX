import uuid
from decimal import Decimal
from rest_framework.test import APITestCase
from rest_framework import status
from core.applications.sales.models import Sale, SaleItem, SaleReturn
from core.applications.transactions.models import Customer
from core.applications.users.models import Membership
from core.applications.users.services import seed_default_roles
from core.applications.inventory.services import create_opening_stock
from core.applications.inventory.tests.factories import (
    OrganizationFactory, UserFactory, StockLocationFactory, ProductFactory
)


class SalesAPITest(APITestCase):
    def setUp(self):
        # 1. Primary Organization & Roles
        self.org = OrganizationFactory()
        self.roles = seed_default_roles(self.org)

        # 2. Owner user (all permissions)
        self.owner = UserFactory()
        self.owner_membership = Membership.objects.create(
            user=self.owner,
            organization=self.org,
            role=self.roles["owner"],
            is_active=True,
        )

        # 3. Manager user (has CREATE_SALE, APPLY_DISCOUNT, CREATE_SALE_RETURN)
        self.manager = UserFactory()
        self.manager_membership = Membership.objects.create(
            user=self.manager,
            organization=self.org,
            role=self.roles["manager"],
            is_active=True,
        )

        # 4. Sales Representative (has CREATE_SALE, but lacks APPLY_DISCOUNT and CREATE_SALE_RETURN)
        self.sales_rep = UserFactory()
        self.sales_rep_membership = Membership.objects.create(
            user=self.sales_rep,
            organization=self.org,
            role=self.roles["sales-representative"],
            is_active=True,
        )

        # 5. Secondary Organization & User for Tenant Boundary Isolation
        self.other_org = OrganizationFactory()
        self.other_roles = seed_default_roles(self.other_org)
        self.other_user = UserFactory()
        self.other_membership = Membership.objects.create(
            user=self.other_user,
            organization=self.other_org,
            role=self.other_roles["owner"],
            is_active=True,
        )

        # 6. Resources & Initial Stock
        self.location = StockLocationFactory(warehouse__organization=self.org)
        self.product = ProductFactory(organization=self.org)
        self.customer = Customer.objects.create(organization=self.org, name="Test Customer")

        # Provision warehouse stock so sales stock deduction passes
        create_opening_stock(self.product, None, None, self.location.warehouse, 100, self.owner)

        self.sale = Sale.objects.create(
            sale_id=f"SALE-{uuid.uuid4().hex[:6]}",
            organization=self.org,
            customer=self.customer,
            location=self.location,
            sales_rep=self.owner,
            subtotal=Decimal("30.00"),
            total=Decimal("30.00"),
        )
        self.sale_item = SaleItem.objects.create(
            sale=self.sale,
            product=self.product,
            product_name=self.product.name,
            quantity=2,
            unit_price=Decimal("15.00"),
        )

        # Default authenticated as owner
        self.client.force_authenticate(user=self.owner)

    # -------------------------------------------------------------------------
    # Owner Lifecycle & Round-Trip Tests
    # -------------------------------------------------------------------------
    def test_list_sales(self):
        """Owner can list sales scoped to their organization."""
        url = "/api/sales/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data.get('results', []) if isinstance(response.data, dict) else response.data
        self.assertGreaterEqual(len(results), 1)
        sale_ids = [str(r['id']).replace('-', '') for r in results]
        self.assertIn(str(self.sale.id).replace('-', ''), sale_ids)

    def test_owner_create_sale_round_trip(self):
        """
        Owner creates sale; verify DB record and follow-up GET detail endpoint round-trip.
        """
        url = "/api/sales/"
        data = {
            "organization": str(self.org.id),
            "customer": str(self.customer.id),
            "location": str(self.location.id),
            "items": [
                {
                    "product": str(self.product.id),
                    "product_name": self.product.name,
                    "quantity": 3,
                    "unit_price": "20.00",
                }
            ]
        }
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        created_id = response.data['id']

        # 1. DB verification
        created_sale = Sale.objects.get(id=created_id)
        self.assertEqual(created_sale.items.count(), 1)
        self.assertEqual(str(created_sale.customer_id).replace('-', ''), str(self.customer.id).replace('-', ''))

        # 2. Round-Trip GET verification
        get_resp = self.client.get(f"/api/sales/{created_id}/")
        self.assertEqual(get_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(get_resp.data['items']), 1)

    def test_owner_can_apply_discount(self):
        """Owner holds APPLY_DISCOUNT: sale creation with discount succeeds."""
        url = "/api/sales/"
        data = {
            "organization": str(self.org.id),
            "customer": str(self.customer.id),
            "location": str(self.location.id),
            "discount_amount": "5.00",
            "items": [
                {
                    "product": str(self.product.id),
                    "product_name": self.product.name,
                    "quantity": 2,
                    "unit_price": "25.00",
                    "discount": "2.00",
                }
            ]
        }
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    # -------------------------------------------------------------------------
    # Sales Representative Role Permissions
    # -------------------------------------------------------------------------
    def test_sales_rep_can_create_sale_without_discount(self):
        """Sales Rep has CREATE_SALE: creating normal sales without discounts succeeds."""
        self.client.force_authenticate(user=self.sales_rep)
        url = "/api/sales/"
        data = {
            "organization": str(self.org.id),
            "customer": str(self.customer.id),
            "location": str(self.location.id),
            "items": [
                {
                    "product": str(self.product.id),
                    "product_name": self.product.name,
                    "quantity": 1,
                    "unit_price": "15.00",
                }
            ]
        }
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_sales_rep_cannot_apply_discount(self):
        """Sales Rep lacks APPLY_DISCOUNT: submitting discount returns 400 Bad Request."""
        self.client.force_authenticate(user=self.sales_rep)
        url = "/api/sales/"
        data = {
            "organization": str(self.org.id),
            "customer": str(self.customer.id),
            "location": str(self.location.id),
            "discount_amount": "10.00",
            "items": [
                {
                    "product": str(self.product.id),
                    "product_name": self.product.name,
                    "quantity": 1,
                    "unit_price": "50.00",
                }
            ]
        }
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        errors = response.data.get("errors", response.data)
        self.assertIn("discount_amount", errors)

    def test_sales_rep_cannot_create_sale_return(self):
        """Sales Rep lacks CREATE_SALE_RETURN -> 403 Forbidden."""
        self.client.force_authenticate(user=self.sales_rep)
        url = "/api/sales/returns/"
        data = {
            "original_sale": str(self.sale.id),
            "organization": str(self.org.id),
            "return_reason": "Defective item",
            "refund_amount": "15.00",
            "items": [
                {
                    "sale_item": str(self.sale_item.id),
                    "quantity": 1,
                    "refund_amount": "15.00",
                }
            ]
        }
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    # -------------------------------------------------------------------------
    # Manager Role Permissions
    # -------------------------------------------------------------------------
    def test_manager_can_create_sale_return(self):
        """Manager has CREATE_SALE_RETURN: can process customer returns."""
        self.client.force_authenticate(user=self.manager)
        url = "/api/sales/returns/"
        data = {
            "original_sale": str(self.sale.id),
            "organization": str(self.org.id),
            "return_reason": "Customer changed mind",
            "refund_amount": "15.00",
            "items": [
                {
                    "sale_item": str(self.sale_item.id),
                    "quantity": 1,
                    "refund_amount": "15.00",
                }
            ]
        }
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(SaleReturn.objects.filter(original_sale=self.sale).exists())

    # -------------------------------------------------------------------------
    # Multi-Tenant Boundary Isolation Tests
    # -------------------------------------------------------------------------
    def test_tenant_isolation_sales(self):
        """User from Org B cannot retrieve Org A's sale or create sales in Org A."""
        self.client.force_authenticate(user=self.other_user)

        # Cannot see in list
        list_resp = self.client.get("/api/sales/")
        self.assertEqual(list_resp.status_code, status.HTTP_200_OK)
        results = list_resp.data.get('results', []) if isinstance(list_resp.data, dict) else list_resp.data
        sale_ids = [str(r['id']).replace('-', '') for r in results]
        self.assertNotIn(str(self.sale.id).replace('-', ''), sale_ids)

        # Cannot retrieve Org A's sale -> 404
        get_resp = self.client.get(f"/api/sales/{self.sale.id}/")
        self.assertEqual(get_resp.status_code, status.HTTP_404_NOT_FOUND)

        # Cannot create sale for Org A -> 403
        create_resp = self.client.post("/api/sales/", {
            "organization": str(self.org.id),
            "customer": str(self.customer.id),
            "location": str(self.location.id),
            "items": []
        }, format="json")
        self.assertEqual(create_resp.status_code, status.HTTP_403_FORBIDDEN)
