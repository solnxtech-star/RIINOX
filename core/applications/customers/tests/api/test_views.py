from __future__ import annotations

from decimal import Decimal
from rest_framework import status
from rest_framework.test import APITestCase

from core.applications.customers.models import Customer, CustomerAddress
from core.applications.customers.tests.factories import CustomerAddressFactory, CustomerFactory
from core.applications.inventory.tests.factories import OrganizationFactory, UserFactory
from core.applications.sales.models import Sale
from core.applications.warehouse.models import StockLocation, Warehouse
from core.applications.users.models import Membership
from core.applications.users.services import seed_default_roles
from core.helper.enums import CustomerStatus, CustomerType


class CustomerAPITest(APITestCase):
    def setUp(self):
        # 1. Organization & Roles
        self.org = OrganizationFactory()
        self.roles = seed_default_roles(self.org)

        # 2. Owner (all permissions, including MANAGE_CUSTOMER_CREDIT)
        self.owner = UserFactory()
        self.owner_membership = Membership.objects.create(
            user=self.owner,
            organization=self.org,
            role=self.roles["owner"],
            is_active=True,
        )

        # 3. Manager (has MANAGE_CUSTOMERS, lacks MANAGE_CUSTOMER_CREDIT)
        self.manager = UserFactory()
        self.manager_membership = Membership.objects.create(
            user=self.manager,
            organization=self.org,
            role=self.roles["manager"],
            is_active=True,
        )

        # 4. Sales Representative (has VIEW_CUSTOMERS & MANAGE_CUSTOMERS, lacks MANAGE_CUSTOMER_CREDIT)
        self.sales_rep = UserFactory()
        self.sales_rep_membership = Membership.objects.create(
            user=self.sales_rep,
            organization=self.org,
            role=self.roles["sales-representative"],
            is_active=True,
        )

        # 5. Inventory Staff (no customer permissions)
        self.staff_user = UserFactory()
        self.staff_membership = Membership.objects.create(
            user=self.staff_user,
            organization=self.org,
            role=self.roles["inventory-staff"],
            is_active=True,
        )

        # 6. Other Org & Other User for Tenant Isolation
        self.other_org = OrganizationFactory()
        self.other_roles = seed_default_roles(self.other_org)
        self.other_user = UserFactory()
        self.other_membership = Membership.objects.create(
            user=self.other_user,
            organization=self.other_org,
            role=self.other_roles["owner"],
            is_active=True,
        )

        # 7. Seed sample customer
        self.customer = CustomerFactory(
            organization=self.org,
            name="Alpha Enterprises",
            company_name="Alpha Ltd",
            email="alpha@example.com",
            phone="+2348012345678",
            customer_type=CustomerType.BUSINESS,
            credit_limit=Decimal("50000.00"),
            payment_terms_days=30,
            status=CustomerStatus.ACTIVE,
        )
        self.customer_address = CustomerAddressFactory(
            customer=self.customer,
            street="1 Commercial Avenue",
            city="Ikeja",
            state="Lagos",
            is_default=True,
        )

        # Base URLs
        self.base_url = f"/api/organizations/{self.org.id}/customers/"
        self.detail_url = f"{self.base_url}{self.customer.id}/"

        # Default auth: owner
        self.client.force_authenticate(user=self.owner)

    # -------------------------------------------------------------------------
    # List & Retrieve
    # -------------------------------------------------------------------------
    def test_list_customers(self):
        response = self.client.get(self.base_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data.get("results", response.data)
        self.assertGreaterEqual(len(results), 1)
        self.assertEqual(str(results[0]["id"]).replace("-", ""), str(self.customer.id).replace("-", ""))

    def test_list_customers_filtering_and_search(self):
        CustomerFactory(
            organization=self.org,
            name="Beta Pharmacy",
            status=CustomerStatus.INACTIVE,
            customer_type=CustomerType.INDIVIDUAL,
        )

        # Filter by status
        res_active = self.client.get(f"{self.base_url}?status=active")
        self.assertEqual(res_active.status_code, status.HTTP_200_OK)
        results_active = res_active.data.get("results", res_active.data)
        self.assertTrue(all(c["status"] == "active" for c in results_active))

        # Filter by customer_type
        res_type = self.client.get(f"{self.base_url}?customer_type=business")
        self.assertEqual(res_type.status_code, status.HTTP_200_OK)
        results_type = res_type.data.get("results", res_type.data)
        self.assertTrue(all(c["customer_type"] == "business" for c in results_type))

        # Search by name
        res_search = self.client.get(f"{self.base_url}?search=Beta")
        self.assertEqual(res_search.status_code, status.HTTP_200_OK)
        results_search = res_search.data.get("results", res_search.data)
        self.assertEqual(len(results_search), 1)
        self.assertEqual(results_search[0]["name"], "Beta Pharmacy")

    def test_retrieve_customer(self):
        response = self.client.get(self.detail_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["name"], "Alpha Enterprises")
        self.assertEqual(len(response.data["addresses"]), 1)
        self.assertEqual(response.data["addresses"][0]["city"], "Ikeja")

    # -------------------------------------------------------------------------
    # Create Customer
    # -------------------------------------------------------------------------
    def test_create_customer_success(self):
        payload = {
            "name": "Delta Logistics",
            "customer_type": "business",
            "company_name": "Delta Global Inc",
            "email": "contact@deltaglobal.com",
            "phone": "+2348098765432",
            "metadata": {"custom_tag": "vip"},
            "addresses": [
                {
                    "address_type": "shipping",
                    "street": "45 Wharf Road",
                    "city": "Apapa",
                    "state": "Lagos",
                    "country": "Nigeria",
                    "is_default": True,
                }
            ],
        }
        response = self.client.post(self.base_url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        new_id = response.data["id"]

        customer = Customer.objects.get(id=new_id)
        self.assertEqual(str(customer.organization_id).replace("-", ""), str(self.org.id).replace("-", ""))
        self.assertEqual(customer.name, "Delta Logistics")
        self.assertEqual(customer.credit_limit, Decimal("0.00"))
        self.assertEqual(customer.addresses.count(), 1)

    def test_create_customer_credit_without_permission_fails(self):
        self.client.force_authenticate(user=self.sales_rep)
        payload = {
            "name": "Unauthorized Credit Client",
            "credit_limit": "25000.00",
        }
        response = self.client.post(self.base_url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        err_dict = response.data.get("errors", response.data)
        self.assertIn("credit_limit", err_dict)

    def test_create_customer_cross_tenant_spoofing_rejected(self):
        payload = {
            "name": "Spoofed Client",
            "organization": str(self.other_org.id),
        }
        response = self.client.post(self.base_url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        err_dict = response.data.get("errors", response.data)
        self.assertIn("organization", err_dict)

    # -------------------------------------------------------------------------
    # Update Customer
    # -------------------------------------------------------------------------
    def test_update_customer_success(self):
        payload = {
            "name": "Alpha Enterprises International",
            "notes": "Premium client tier",
            "addresses": [
                {
                    "address_type": "billing",
                    "street": "100 Broad Street",
                    "city": "Lagos Island",
                    "state": "Lagos",
                    "country": "Nigeria",
                    "is_default": True,
                }
            ],
        }
        response = self.client.patch(self.detail_url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.name, "Alpha Enterprises International")
        self.assertEqual(self.customer.addresses.first().city, "Lagos Island")

    def test_update_customer_credit_without_permission_fails(self):
        self.client.force_authenticate(user=self.sales_rep)
        payload = {"credit_limit": "99000.00"}
        response = self.client.patch(self.detail_url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        err_dict = response.data.get("errors", response.data)
        self.assertIn("credit_limit", err_dict)

    # -------------------------------------------------------------------------
    # Destroy & Soft-Deactivation Fallback (PRD §44)
    # -------------------------------------------------------------------------
    def test_destroy_unreferenced_customer_hard_deletes(self):
        temp_cust = CustomerFactory(organization=self.org)
        url = f"{self.base_url}{temp_cust.id}/"

        response = self.client.delete(url)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Customer.objects.filter(id=temp_cust.id).exists())

    def test_destroy_referenced_customer_soft_deactivates(self):
        warehouse = Warehouse.objects.create(organization=self.org, name="WH Main", code="WHM")
        loc = StockLocation.objects.create(warehouse=warehouse, name="Loc 1")
        Sale.objects.create(
            sale_id="SALE-TEST-999",
            organization=self.org,
            customer=self.customer,
            location=loc,
            sales_rep=self.owner,
        )

        response = self.client.delete(self.detail_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("deactivated instead of deleted", response.data["detail"])

        self.customer.refresh_from_db()
        self.assertEqual(self.customer.status, CustomerStatus.INACTIVE)

    # -------------------------------------------------------------------------
    # Custom Action: adjust_credit (PRD §26)
    # -------------------------------------------------------------------------
    def test_adjust_credit_action_success_with_permission(self):
        url = f"{self.detail_url}adjust_credit/"
        payload = {
            "credit_limit": "75000.00",
            "payment_terms_days": 45,
            "reason": "Quarterly credit limit expansion",
        }
        response = self.client.post(url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.credit_limit, Decimal("75000.00"))
        self.assertEqual(self.customer.payment_terms_days, 45)

    def test_adjust_credit_action_forbidden_without_permission(self):
        self.client.force_authenticate(user=self.manager)
        url = f"{self.detail_url}adjust_credit/"
        payload = {
            "credit_limit": "75000.00",
            "payment_terms_days": 45,
        }
        response = self.client.post(url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    # -------------------------------------------------------------------------
    # Custom Action: sales (PRD §20)
    # -------------------------------------------------------------------------
    def test_customer_sales_action(self):
        warehouse = Warehouse.objects.create(organization=self.org, name="WH East", code="WHE")
        loc = StockLocation.objects.create(warehouse=warehouse, name="Loc 2")
        sale = Sale.objects.create(
            sale_id="SALE-HIST-001",
            organization=self.org,
            customer=self.customer,
            location=loc,
            sales_rep=self.owner,
        )

        url = f"{self.detail_url}sales/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data.get("results", response.data)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["sale_id"], "SALE-HIST-001")

    # -------------------------------------------------------------------------
    # Tenant Boundary Isolation
    # -------------------------------------------------------------------------
    def test_tenant_boundary_isolation(self):
        self.client.force_authenticate(user=self.other_user)

        # Accessing Org A endpoint returns 404
        self.assertEqual(self.client.get(self.base_url).status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(self.client.get(self.detail_url).status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(
            self.client.post(self.base_url, {"name": "Intruder"}, format="json").status_code,
            status.HTTP_404_NOT_FOUND,
        )
        self.assertEqual(self.client.delete(self.detail_url).status_code, status.HTTP_404_NOT_FOUND)

        # Accessing own Org B succeeds without leaking Org A customer
        other_base_url = f"/api/organizations/{self.other_org.id}/customers/"
        resp = self.client.get(other_base_url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        results = resp.data.get("results", resp.data)
        self.assertNotIn(str(self.customer.id).replace("-", ""), [str(r["id"]).replace("-", "") for r in results])
