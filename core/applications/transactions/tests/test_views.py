from __future__ import annotations

from rest_framework import status
from rest_framework.test import APITestCase

from core.applications.inventory.tests.factories import (
    OrganizationFactory,
    UserFactory,
)
from core.applications.users.models import Membership
from core.applications.users.services import seed_default_roles
from core.applications.transactions.models import (
    Transaction,
    FinancialLedgerEntry,
    Customer,
)
from core.helper.enums import (
    TransactionTypeChoices,
    TransactionStatusChoices,
    AccountNameChoices,
    CustomerType,
    CustomerStatus,
)


class CustomerAndTransactionAPITest(APITestCase):
    def setUp(self):
        # 1. Organization & Roles
        self.org = OrganizationFactory()
        if hasattr(self.org, "subscription") and self.org.subscription:
            self.org.subscription.plan.max_transactions_per_month = 100
            self.org.subscription.plan.save(update_fields=["max_transactions_per_month"])
        self.roles = seed_default_roles(self.org)

        # 2. Owner user (all permissions)
        self.owner = UserFactory()
        self.owner_membership = Membership.objects.create(
            user=self.owner,
            organization=self.org,
            role=self.roles["owner"],
            is_active=True,
        )

        # 3. Sales Representative (has VIEW_CUSTOMERS & MANAGE_CUSTOMERS, lacks VIEW_FINANCIAL_REPORTS)
        self.sales_rep = UserFactory()
        self.sales_rep_membership = Membership.objects.create(
            user=self.sales_rep,
            organization=self.org,
            role=self.roles["sales-representative"],
            is_active=True,
        )

        # 4. Inventory Staff (lacks VIEW_CUSTOMERS, MANAGE_CUSTOMERS, VIEW_FINANCIAL_REPORTS)
        self.inventory_staff = UserFactory()
        self.staff_membership = Membership.objects.create(
            user=self.inventory_staff,
            organization=self.org,
            role=self.roles["inventory-staff"],
            is_active=True,
        )

        # 4. Other Org & Other User for Tenant Boundary Isolation
        self.other_org = OrganizationFactory()
        if hasattr(self.other_org, "subscription") and self.other_org.subscription:
            self.other_org.subscription.plan.max_transactions_per_month = 100
            self.other_org.subscription.plan.save(update_fields=["max_transactions_per_month"])
        self.other_roles = seed_default_roles(self.other_org)
        self.other_user = UserFactory()
        self.other_membership = Membership.objects.create(
            user=self.other_user,
            organization=self.other_org,
            role=self.other_roles["owner"],
            is_active=True,
        )

        # 5. Base Resources
        self.customer = Customer.objects.create(
            organization=self.org,
            name="Acme Corp",
            email="acme@example.com",
            phone="08012345678",
            customer_type=CustomerType.BUSINESS if hasattr(CustomerType, "BUSINESS") else CustomerType.INDIVIDUAL,
        )
        self.transaction = Transaction.objects.create(
            organization=self.org,
            transaction_id="TRX-TEST-0001",
            transaction_type=TransactionTypeChoices.SALE,
            status=TransactionStatusChoices.COMPLETED,
            created_by=self.owner,
            notes="Initial sale event",
        )
        self.ledger_entry = FinancialLedgerEntry.objects.create(
            transaction=self.transaction,
            account_name=AccountNameChoices.CASH_BANK,
            debit=50000,
            credit=0,
        )

        # Authenticate as owner
        self.client.force_authenticate(user=self.owner)
        self.customer_base_url = f"/api/organizations/{self.org.id}/customers/"
        self.customer_detail_url = f"{self.customer_base_url}{self.customer.id}/"
        self.trx_base_url = f"/api/organizations/{self.org.id}/transactions/"
        self.trx_detail_url = f"{self.trx_base_url}{self.transaction.id}/"

    def test_list_customers(self):
        """Owner can list customers scoped to their organization."""
        response = self.client.get(self.customer_base_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data.get("results", []) if isinstance(response.data, dict) else response.data
        self.assertGreaterEqual(len(results), 1)
        self.assertEqual(str(results[0]["id"]).replace("-", ""), str(self.customer.id).replace("-", ""))

    def test_retrieve_customer(self):
        """Owner can retrieve customer detail."""
        response = self.client.get(self.customer_detail_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["name"], "Acme Corp")
        self.assertEqual(response.data["email"], "acme@example.com")

    def test_create_customer(self):
        """Owner can create a new customer for their organization."""
        data = {
            "name": "Global Logistics Ltd",
            "email": "info@globallogistics.com",
            "phone": "08098765432",
        }
        response = self.client.post(self.customer_base_url, data, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        new_cust_id = response.data["id"]

        new_cust = Customer.objects.get(id=new_cust_id)
        self.assertEqual(str(new_cust.organization_id).replace("-", ""), str(self.org.id).replace("-", ""))
        self.assertEqual(new_cust.name, "Global Logistics Ltd")

    def test_update_customer(self):
        """Owner can update customer metadata."""
        data = {"address": "Suite 404, Port Harcourt"}
        response = self.client.patch(self.customer_detail_url, data, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.address, "Suite 404, Port Harcourt")

    def test_delete_customer(self):
        """Owner can delete customer."""
        temp_cust = Customer.objects.create(
            organization=self.org,
            name="Temporary Customer",
        )
        url = f"{self.customer_base_url}{temp_cust.id}/"
        response = self.client.delete(url)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Customer.objects.filter(id=temp_cust.id).exists())

    def test_list_transactions(self):
        """Owner can list transactions scoped to their organization."""
        response = self.client.get(self.trx_base_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data.get("results", []) if isinstance(response.data, dict) else response.data
        self.assertGreaterEqual(len(results), 1)
        self.assertEqual(str(results[0]["id"]).replace("-", ""), str(self.transaction.id).replace("-", ""))

    def test_retrieve_transaction(self):
        """Owner can retrieve transaction with nested financial entries."""
        response = self.client.get(self.trx_detail_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["transaction_id"], "TRX-TEST-0001")
        self.assertEqual(len(response.data["financial_entries"]), 1)
        self.assertEqual(response.data["financial_entries"][0]["account_name"], AccountNameChoices.CASH_BANK)

    def test_create_transaction(self):
        """Owner can create a transaction."""
        data = {
            "transaction_type": TransactionTypeChoices.PURCHASE,
            "status": TransactionStatusChoices.COMPLETED,
            "notes": "Direct vendor procurement transaction",
        }
        response = self.client.post(self.trx_base_url, data, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        new_trx_id = response.data["id"]

        new_trx = Transaction.objects.get(id=new_trx_id)
        self.assertEqual(str(new_trx.organization_id).replace("-", ""), str(self.org.id).replace("-", ""))
        self.assertEqual(new_trx.transaction_type, TransactionTypeChoices.PURCHASE)

    # -------------------------------------------------------------------------
    # Granular Role-Based Permission Tests
    # -------------------------------------------------------------------------
    def test_role_based_permissions(self):
        """
        - Sales Rep has VIEW_CUSTOMERS & MANAGE_CUSTOMERS, lacks VIEW_FINANCIAL_REPORTS.
        - Inventory Staff lacks VIEW_CUSTOMERS and VIEW_FINANCIAL_REPORTS.
        """
        # 1. Sales Rep checks
        self.client.force_authenticate(user=self.sales_rep)
        cust_list = self.client.get(self.customer_base_url)
        self.assertEqual(cust_list.status_code, status.HTTP_200_OK)

        cust_create = self.client.post(self.customer_base_url, {
            "name": "Rep Client",
        }, format="json")
        self.assertEqual(cust_create.status_code, status.HTTP_201_CREATED)

        trx_list = self.client.get(self.trx_base_url)
        self.assertEqual(trx_list.status_code, status.HTTP_403_FORBIDDEN)

        # 2. Inventory Staff checks
        self.client.force_authenticate(user=self.inventory_staff)
        staff_cust_list = self.client.get(self.customer_base_url)
        self.assertEqual(staff_cust_list.status_code, status.HTTP_403_FORBIDDEN)

        staff_trx_list = self.client.get(self.trx_base_url)
        self.assertEqual(staff_trx_list.status_code, status.HTTP_403_FORBIDDEN)

    # -------------------------------------------------------------------------
    # Multi-Tenant Boundary Isolation Tests
    # -------------------------------------------------------------------------
    def test_tenant_isolation_non_member_access_returns_404(self):
        """User from Org B accessing Org A endpoints receives 404 Not Found."""
        self.client.force_authenticate(user=self.other_user)

        # Org A Customer endpoints return 404
        self.assertEqual(self.client.get(self.customer_base_url).status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(self.client.get(self.customer_detail_url).status_code, status.HTTP_404_NOT_FOUND)
        cust_post = self.client.post(self.customer_base_url, {"name": "Injected Client"}, format="json")
        self.assertEqual(cust_post.status_code, status.HTTP_404_NOT_FOUND)

        # Org A Transaction endpoints return 404
        self.assertEqual(self.client.get(self.trx_base_url).status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(self.client.get(self.trx_detail_url).status_code, status.HTTP_404_NOT_FOUND)
        trx_post = self.client.post(self.trx_base_url, {
            "transaction_type": TransactionTypeChoices.SALE,
        }, format="json")
        self.assertEqual(trx_post.status_code, status.HTTP_404_NOT_FOUND)

        # Accessing own Org B endpoints is allowed and does not expose Org A data
        other_cust_url = f"/api/organizations/{self.other_org.id}/customers/"
        own_cust_list = self.client.get(other_cust_url)
        self.assertEqual(own_cust_list.status_code, status.HTTP_200_OK)
        results = own_cust_list.data.get("results", []) if isinstance(own_cust_list.data, dict) else own_cust_list.data
        cust_ids = [str(r["id"]).replace("-", "") for r in results]
        self.assertNotIn(str(self.customer.id).replace("-", ""), cust_ids)

        other_trx_url = f"/api/organizations/{self.other_org.id}/transactions/"
        own_trx_list = self.client.get(other_trx_url)
        self.assertEqual(own_trx_list.status_code, status.HTTP_200_OK)
        results = own_trx_list.data.get("results", []) if isinstance(own_trx_list.data, dict) else own_trx_list.data
        trx_ids = [str(r["id"]).replace("-", "") for r in results]
        self.assertNotIn(str(self.transaction.id).replace("-", ""), trx_ids)
