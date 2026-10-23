import uuid
from decimal import Decimal
from rest_framework.test import APITestCase
from rest_framework import status
from core.applications.sales.models import Sale, SaleItem, SaleReturn
from core.applications.customers.models import Customer
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

    @property
    def sales_url(self):
        return f"/api/organizations/{self.org.id}/sales/"

    @property
    def returns_url(self):
        return f"/api/organizations/{self.org.id}/sales/returns/"

    # -------------------------------------------------------------------------
    # Owner Lifecycle & Round-Trip Tests
    # -------------------------------------------------------------------------
    def test_list_sales(self):
        """Owner can list sales scoped to their organization."""
        url = self.sales_url
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
        url = self.sales_url
        data = {
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
        get_resp = self.client.get(f"{self.sales_url}{created_id}/")
        self.assertEqual(get_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(get_resp.data['items']), 1)

    def test_owner_can_apply_discount(self):
        """Owner holds APPLY_DISCOUNT: sale creation with discount succeeds."""
        url = self.sales_url
        data = {
            "customer": str(self.customer.id),
            "location": str(self.location.id),
            "discount_amount": "5.00",
            "items": [
                {
                    "product": str(self.product.id),
                    "product_name": self.product.name,
                    "quantity": 2,
                    "unit_price": "25.00",
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
        url = self.sales_url
        data = {
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
        url = self.sales_url
        data = {
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
        url = self.returns_url
        data = {
            "original_sale": str(self.sale.id),
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
        url = self.returns_url
        data = {
            "original_sale": str(self.sale.id),
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
        """User from Org B cannot access Org A's tenant-scoped endpoints."""
        self.client.force_authenticate(user=self.other_user)

        # Accessing Org A's URL when belonging to Org B -> 404 Not Found
        list_resp = self.client.get(f"/api/organizations/{self.org.id}/sales/")
        self.assertEqual(list_resp.status_code, status.HTTP_404_NOT_FOUND)

        # Cannot retrieve Org A's sale -> 404
        get_resp = self.client.get(f"/api/organizations/{self.org.id}/sales/{self.sale.id}/")
        self.assertEqual(get_resp.status_code, status.HTTP_404_NOT_FOUND)

        # Cannot create sale in Org A -> 404
        create_resp = self.client.post(f"/api/organizations/{self.org.id}/sales/", {
            "customer": str(self.customer.id),
            "location": str(self.location.id),
            "items": []
        }, format="json")
        self.assertEqual(create_resp.status_code, status.HTTP_404_NOT_FOUND)

    # -------------------------------------------------------------------------
    # Authentication & Inactive Membership Tests
    # -------------------------------------------------------------------------
    def test_unauthenticated_cannot_access_sales_endpoints(self):
        """Unauthenticated requests are rejected with 401 Unauthorized."""
        self.client.logout()

        # 1. Sales list
        resp_list = self.client.get(self.sales_url)
        self.assertEqual(resp_list.status_code, status.HTTP_401_UNAUTHORIZED)

        # 2. Sales detail
        resp_detail = self.client.get(f"{self.sales_url}{self.sale.id}/")
        self.assertEqual(resp_detail.status_code, status.HTTP_401_UNAUTHORIZED)

        # 3. Sales create
        resp_create = self.client.post(self.sales_url, {}, format="json")
        self.assertEqual(resp_create.status_code, status.HTTP_401_UNAUTHORIZED)

        # 4. Returns list
        resp_ret_list = self.client.get(self.returns_url)
        self.assertEqual(resp_ret_list.status_code, status.HTTP_401_UNAUTHORIZED)

        # 5. Returns create
        resp_ret_create = self.client.post(self.returns_url, {}, format="json")
        self.assertEqual(resp_ret_create.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_inactive_membership_blocks_sales_access(self):
        """User with inactive membership (is_active=False) cannot access sales."""
        inactive_user = UserFactory()
        Membership.objects.create(
            user=inactive_user,
            organization=self.org,
            role=self.roles["sales-representative"],
            is_active=False,
        )
        self.client.force_authenticate(user=inactive_user)

        # Cannot list sales -> 404 (non-member / inactive member blocked from tenant path)
        resp_list = self.client.get(self.sales_url)
        self.assertEqual(resp_list.status_code, status.HTTP_404_NOT_FOUND)

        # Cannot retrieve sale -> 404 Not Found
        resp_detail = self.client.get(f"{self.sales_url}{self.sale.id}/")
        self.assertEqual(resp_detail.status_code, status.HTTP_404_NOT_FOUND)

        # Cannot create sale -> 404 Not Found
        resp_create = self.client.post(self.sales_url, {
            "customer": str(self.customer.id),
            "location": str(self.location.id),
            "items": []
        }, format="json")
        self.assertEqual(resp_create.status_code, status.HTTP_404_NOT_FOUND)

    # -------------------------------------------------------------------------
    # Granular Role Permission Tests
    # -------------------------------------------------------------------------
    def test_user_lacking_view_sales_cannot_list_or_retrieve(self):
        """Teacher/Staff lacks VIEW_SALES: cannot list or retrieve sales."""
        teacher_user = UserFactory()
        Membership.objects.create(
            user=teacher_user,
            organization=self.org,
            role=self.roles["teacher-staff"],
            is_active=True,
        )
        self.client.force_authenticate(user=teacher_user)

        # List -> 403
        resp_list = self.client.get(self.sales_url)
        self.assertEqual(resp_list.status_code, status.HTTP_403_FORBIDDEN)

        # Detail -> 403
        resp_detail = self.client.get(f"{self.sales_url}{self.sale.id}/")
        self.assertEqual(resp_detail.status_code, status.HTTP_403_FORBIDDEN)

    def test_accountant_can_view_sales_but_cannot_create_sales(self):
        """Accountant holds VIEW_SALES but lacks CREATE_SALE."""
        accountant = UserFactory()
        Membership.objects.create(
            user=accountant,
            organization=self.org,
            role=self.roles["accountant"],
            is_active=True,
        )
        self.client.force_authenticate(user=accountant)

        # Can list -> 200
        resp_list = self.client.get(self.sales_url)
        self.assertEqual(resp_list.status_code, status.HTTP_200_OK)

        # Can retrieve -> 200
        resp_detail = self.client.get(f"{self.sales_url}{self.sale.id}/")
        self.assertEqual(resp_detail.status_code, status.HTTP_200_OK)

        # Cannot create sale -> 403
        resp_create = self.client.post(self.sales_url, {
            "customer": str(self.customer.id),
            "location": str(self.location.id),
            "items": [
                {
                    "product": str(self.product.id),
                    "product_name": self.product.name,
                    "quantity": 1,
                    "unit_price": "10.00",
                }
            ]
        }, format="json")
        self.assertEqual(resp_create.status_code, status.HTTP_403_FORBIDDEN)

    # -------------------------------------------------------------------------
    # Cross-Tenant Resource Validation Tests
    # -------------------------------------------------------------------------
    def test_cross_tenant_customer_rejected_on_sale_create(self):
        """Cannot create sale in Org A with customer belonging to Org B."""
        other_customer = Customer.objects.create(organization=self.other_org, name="Foreign Customer")
        data = {
            "customer": str(other_customer.id),
            "location": str(self.location.id),
            "items": [
                {
                    "product": str(self.product.id),
                    "product_name": self.product.name,
                    "quantity": 1,
                    "unit_price": "10.00",
                }
            ]
        }
        resp = self.client.post(self.sales_url, data, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        errors = resp.data.get("errors", resp.data)
        self.assertIn("customer", errors)

    def test_cross_tenant_location_rejected_on_sale_create(self):
        """Cannot create sale in Org A with stock location belonging to Org B."""
        other_location = StockLocationFactory(warehouse__organization=self.other_org)
        data = {
            "customer": str(self.customer.id),
            "location": str(other_location.id),
            "items": [
                {
                    "product": str(self.product.id),
                    "product_name": self.product.name,
                    "quantity": 1,
                    "unit_price": "10.00",
                }
            ]
        }
        resp = self.client.post(self.sales_url, data, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        errors = resp.data.get("errors", resp.data)
        self.assertIn("location", errors)

    def test_cross_tenant_product_rejected_on_sale_create(self):
        """Cannot create sale in Org A selling product belonging to Org B."""
        other_product = ProductFactory(organization=self.other_org)
        data = {
            "customer": str(self.customer.id),
            "location": str(self.location.id),
            "items": [
                {
                    "product": str(other_product.id),
                    "product_name": other_product.name,
                    "quantity": 1,
                    "unit_price": "10.00",
                }
            ]
        }
        resp = self.client.post(self.sales_url, data, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        errors = resp.data.get("errors", resp.data)
        self.assertIn("items", errors)

    def test_cross_tenant_sale_return_rejected(self):
        """Cannot create sale return in Org A referencing a sale from Org B."""
        other_location = StockLocationFactory(warehouse__organization=self.other_org)
        other_customer = Customer.objects.create(organization=self.other_org, name="Foreign Customer")
        other_sale = Sale.objects.create(
            sale_id=f"SALE-{uuid.uuid4().hex[:6]}",
            organization=self.other_org,
            customer=other_customer,
            location=other_location,
            sales_rep=self.other_user,
            subtotal=Decimal("50.00"),
            total=Decimal("50.00"),
        )
        data = {
            "original_sale": str(other_sale.id),
            "return_reason": "Attempt cross-tenant return",
            "refund_amount": "10.00",
            "items": []
        }
        resp = self.client.post(self.returns_url, data, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        errors = resp.data.get("errors", resp.data)
        self.assertIn("original_sale", errors)

    def test_sale_return_item_must_belong_to_original_sale(self):
        """Sale return items must belong to the referenced original sale."""
        second_sale = Sale.objects.create(
            sale_id=f"SALE-{uuid.uuid4().hex[:6]}",
            organization=self.org,
            customer=self.customer,
            location=self.location,
            sales_rep=self.owner,
            subtotal=Decimal("20.00"),
            total=Decimal("20.00"),
        )
        unrelated_item = SaleItem.objects.create(
            sale=second_sale,
            product=self.product,
            product_name=self.product.name,
            quantity=1,
            unit_price=Decimal("20.00"),
        )

        data = {
            "original_sale": str(self.sale.id),
            "return_reason": "Returning unrelated item",
            "refund_amount": "20.00",
            "items": [
                {
                    "sale_item": str(unrelated_item.id),
                    "quantity": 1,
                    "refund_amount": "20.00",
                }
            ]
        }
        resp = self.client.post(self.returns_url, data, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        errors = resp.data.get("errors", resp.data)
        self.assertIn("items", errors)

    def test_multi_org_user_scopes_sales_by_url_path(self):
        """
        User belonging to Org A and Org B:
        - URL path determines tenant scope deterministically
        - /organizations/{other_org.id}/sales/ scopes strictly to Org B
        - Foreign organization where user has no membership -> 404 Not Found
        """
        # Add owner to other_org
        Membership.objects.create(
            user=self.owner,
            organization=self.other_org,
            role=self.other_roles["owner"],
            is_active=True,
        )

        # Create sale in other_org
        other_location = StockLocationFactory(warehouse__organization=self.other_org)
        other_customer = Customer.objects.create(organization=self.other_org, name="Foreign Customer")
        other_sale = Sale.objects.create(
            sale_id=f"SALE-{uuid.uuid4().hex[:6]}",
            organization=self.other_org,
            customer=other_customer,
            location=other_location,
            sales_rep=self.other_user,
            subtotal=Decimal("45.00"),
            total=Decimal("45.00"),
        )

        # 1. Primary org path -> scopes strictly to self.org
        resp_default = self.client.get(self.sales_url)
        self.assertEqual(resp_default.status_code, status.HTTP_200_OK)
        results = resp_default.data.get('results', []) if isinstance(resp_default.data, dict) else resp_default.data
        ids = [str(r['id']).replace('-', '') for r in results]
        self.assertIn(str(self.sale.id).replace('-', ''), ids)
        self.assertNotIn(str(other_sale.id).replace('-', ''), ids)

        # 2. Other org path -> scopes strictly to other_org
        resp_other = self.client.get(f"/api/organizations/{self.other_org.id}/sales/")
        self.assertEqual(resp_other.status_code, status.HTTP_200_OK)
        other_results = resp_other.data.get('results', []) if isinstance(resp_other.data, dict) else resp_other.data
        other_ids = [str(r['id']).replace('-', '') for r in other_results]
        self.assertNotIn(str(self.sale.id).replace('-', ''), other_ids)
        self.assertIn(str(other_sale.id).replace('-', ''), other_ids)

        # 3. Foreign organization where user has no membership -> 404 Not Found
        foreign_org = OrganizationFactory()
        resp_foreign = self.client.get(f"/api/organizations/{foreign_org.id}/sales/")
        self.assertEqual(resp_foreign.status_code, status.HTTP_404_NOT_FOUND)

    def test_cancel_sale_endpoint(self):
        """Owner can cancel an order via POST /sales/{id}/cancel/."""
        url = f"{self.sales_url}{self.sale.id}/cancel/"
        response = self.client.post(url, {"reason": "Customer changed mind"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNotNone(response.data["cancelled_at"])
        self.assertEqual(response.data["fulfillment_status"], "canceled")
        self.assertEqual(response.data["cancel_reason"], "Customer changed mind")

    def test_cancel_already_cancelled_sale_fails(self):
        """Cancelling an already-cancelled sale returns HTTP 400 Bad Request."""
        url = f"{self.sales_url}{self.sale.id}/cancel/"
        self.client.post(url, {"reason": "First cancel"}, format="json")
        response = self.client.post(url, {"reason": "Duplicate cancel"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_delete_sale_blocked_by_immutability(self):
        """Hard deleting a sale returns HTTP 400 Bad Request per PRD §44."""
        url = f"{self.sales_url}{self.sale.id}/"
        response = self.client.delete(url)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("cannot be hard-deleted", response.data["detail"])

    def test_delete_sale_return_blocked_by_immutability(self):
        """Hard deleting a sale return returns HTTP 400 Bad Request."""
        sale_return = SaleReturn.objects.create(
            original_sale=self.sale,
            organization=self.org,
            return_reason="Damaged item",
            refund_amount=Decimal("15.00"),
        )
        url = f"{self.returns_url}{sale_return.id}/"
        response = self.client.delete(url)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("cannot be deleted", response.data["detail"])

    def test_sales_filtering_and_searching(self):
        """Sales can be filtered by status and searched by keyword."""
        # Query with match
        resp_match = self.client.get(f"{self.sales_url}?search={self.sale.sale_id}")
        self.assertEqual(resp_match.status_code, status.HTTP_200_OK)
        results = resp_match.data.get("results", resp_match.data)
        self.assertEqual(len(results), 1)

        # Query with non-match
        resp_no_match = self.client.get(f"{self.sales_url}?search=NONEXISTENT_KEYWORD")
        self.assertEqual(resp_no_match.status_code, status.HTTP_200_OK)
        results_empty = resp_no_match.data.get("results", resp_no_match.data)
        self.assertEqual(len(results_empty), 0)

    def test_create_sale_without_location_via_api(self):
        """Client does not provide location in payload; backend auto-resolves active warehouse."""
        url = self.sales_url
        data = {
            "customer": str(self.customer.id),
            "items": [
                {
                    "product": str(self.product.id),
                    "quantity": 2,
                    "unit_price": "20.00",
                }
            ],
        }
        response = self.client.post(url, data, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIn("id", response.data)
        created = Sale.objects.get(id=response.data["id"])
        self.assertEqual(str(created.warehouse_id).replace("-", ""), str(self.location.warehouse_id).replace("-", ""))



