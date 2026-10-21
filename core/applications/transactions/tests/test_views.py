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
)
from core.helper.enums import (
    TransactionTypeChoices,
    TransactionStatusChoices,
    AccountNameChoices,
)


class TransactionAPITest(APITestCase):
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

        # 3. Staff user (lacks VIEW_FINANCIAL_REPORTS)
        self.staff_user = UserFactory()
        self.staff_membership = Membership.objects.create(
            user=self.staff_user,
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
        self.trx_base_url = f"/api/organizations/{self.org.id}/transactions/"
        self.trx_detail_url = f"{self.trx_base_url}{self.transaction.id}/"

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

    def test_role_based_permissions(self):
        """Staff user without VIEW_FINANCIAL_REPORTS receives 403 Forbidden."""
        self.client.force_authenticate(user=self.staff_user)
        response = self.client.get(self.trx_base_url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_tenant_isolation_non_member_access_returns_404(self):
        """User from Org B accessing Org A endpoints receives 404 Not Found."""
        self.client.force_authenticate(user=self.other_user)

        # Org A Transaction endpoints return 404
        self.assertEqual(self.client.get(self.trx_base_url).status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(self.client.get(self.trx_detail_url).status_code, status.HTTP_404_NOT_FOUND)
        trx_post = self.client.post(self.trx_base_url, {
            "transaction_type": TransactionTypeChoices.SALE,
        }, format="json")
        self.assertEqual(trx_post.status_code, status.HTTP_404_NOT_FOUND)

        # Accessing own Org B endpoints is allowed and does not expose Org A data
        other_trx_url = f"/api/organizations/{self.other_org.id}/transactions/"
        own_trx_list = self.client.get(other_trx_url)
        self.assertEqual(own_trx_list.status_code, status.HTTP_200_OK)
        results = own_trx_list.data.get("results", []) if isinstance(own_trx_list.data, dict) else own_trx_list.data
        trx_ids = [str(r["id"]).replace("-", "") for r in results]
        self.assertNotIn(str(self.transaction.id).replace("-", ""), trx_ids)
