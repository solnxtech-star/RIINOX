"""Expenses: recording, ledger posting, editing rules, voiding, permissions and location scope."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from core.applications.expenses.models import Expense
from core.applications.inventory.tests.factories import OrganizationFactory
from core.applications.notification.models import AuditLog
from core.applications.transactions.models import FinancialLedgerEntry
from core.applications.transactions.models import Transaction
from core.applications.users.models import Membership
from core.applications.users.models import MembershipLocation
from core.applications.users.services import seed_default_roles
from core.applications.users.tests.factories import UserFactory
from core.applications.warehouse.models import Warehouse
from core.helper.enums import AccountNameChoices
from core.helper.enums import TransactionTypeChoices
from core.helper.org_time import organization_today


class ExpenseAPITests(TestCase):
    def setUp(self):
        self.org = OrganizationFactory()
        plan = self.org.subscription.plan
        plan.max_locations = None
        plan.max_transactions_per_month = None
        plan.save(update_fields=["max_locations", "max_transactions_per_month"])
        roles = seed_default_roles(self.org)

        self.lekki = Warehouse.objects.create(organization=self.org, name="Lekki", code="LEK", is_default=True)
        self.ikeja = Warehouse.objects.create(organization=self.org, name="Ikeja", code="IKJ")

        self.owner = UserFactory()
        Membership.objects.create(user=self.owner, organization=self.org, role=roles["owner"])
        self.manager = UserFactory()
        manager_membership = Membership.objects.create(
            user=self.manager, organization=self.org, role=roles["manager"], all_locations=False
        )
        MembershipLocation.objects.create(membership=manager_membership, location=self.lekki)
        self.rep = UserFactory()
        Membership.objects.create(user=self.rep, organization=self.org, role=roles["sales-representative"])

        self.client = APIClient()
        self.url = f"/api/organizations/{self.org.pk}/expenses/"

    def _as(self, user):
        self.client.force_authenticate(user)
        return self.client

    def _record(self, user=None, **overrides):
        body = {
            "location": str(self.lekki.pk),
            "category": "fuel",
            "title": "Diesel for generator",
            "amount": "45000.00",
            "payment_method": "cash",
            "paid_to": "Total",
            **overrides,
        }
        return self._as(user or self.owner).post(self.url, body, format="json")

    def test_record_posts_balanced_ledger_and_audit(self):
        response = self._record()

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data["expense_number"], "EXP-000001")
        self.assertEqual(response.data["status"], "recorded")
        self.assertEqual(response.data["expense_date"], organization_today(self.org).isoformat())
        self.assertEqual(response.data["location"]["name"], "Lekki")

        trx = Transaction.objects.get(transaction_type=TransactionTypeChoices.EXPENSE)
        self.assertEqual(str(trx.location_id).replace("-", ""), str(self.lekki.pk).replace("-", ""))
        lines = {e.account_name: (e.debit, e.credit) for e in FinancialLedgerEntry.objects.filter(transaction=trx)}
        self.assertEqual(lines[AccountNameChoices.OPERATING_EXPENSES], (Decimal("45000.00"), Decimal("0.00")))
        self.assertEqual(lines[AccountNameChoices.CASH_BANK], (Decimal("0.00"), Decimal("45000.00")))
        self.assertTrue(AuditLog.objects.filter(action="expense.recorded").exists())

        self.assertEqual(self._record().data["expense_number"], "EXP-000002")

    def test_validation(self):
        future = (organization_today(self.org) + timedelta(days=1)).isoformat()
        response = self._record(expense_date=future)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["code"], "EXPENSE_DATE_IN_FUTURE")

        for bad in ({"amount": "0"}, {"category": "lunch"}, {"payment_method": "barter"}):
            response = self._record(**bad)
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, bad)
            self.assertIn(next(iter(bad)), response.data["errors"])

        other_org_wh = Warehouse.objects.create(organization=OrganizationFactory(), name="X", code="X")
        response = self._record(location=str(other_org_wh.pk))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("location", response.data["errors"])
        self.assertEqual(Expense.objects.count(), 0)

    def test_edit_descriptive_fields_only(self):
        expense_id = self._record().data["id"]
        detail = f"{self.url}{expense_id}/"

        response = self.client.patch(detail, {"title": "Diesel (50L)", "notes": "Gen 2"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["title"], "Diesel (50L)")
        self.assertTrue(AuditLog.objects.filter(action="expense.updated").exists())

        response = self.client.patch(detail, {"amount": "1.00"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("amount", response.data["errors"])
        self.assertEqual(self.client.put(detail, {}, format="json").status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self.assertEqual(self.client.delete(detail).status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    def test_void_reverses_ledger_and_is_final(self):
        expense_id = self._record().data["id"]
        void_url = f"{self.url}{expense_id}/void/"

        self.assertEqual(self.client.post(void_url, {}, format="json").status_code, status.HTTP_400_BAD_REQUEST)
        response = self.client.post(void_url, {"reason": "Entered twice"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["status"], "void")
        self.assertEqual(response.data["void_reason"], "Entered twice")

        net = FinancialLedgerEntry.objects.filter(account_name=AccountNameChoices.OPERATING_EXPENSES)
        self.assertEqual(sum(e.debit - e.credit for e in net), Decimal("0.00"))

        again = self.client.post(void_url, {"reason": "again"}, format="json")
        self.assertEqual(again.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(again.data["code"], "EXPENSE_ALREADY_VOID")
        edit = self.client.patch(f"{self.url}{expense_id}/", {"title": "x"}, format="json")
        self.assertEqual(edit.data["code"], "EXPENSE_NOT_EDITABLE")

    def test_permissions_and_location_scope(self):
        # Sales reps have no expense permissions at all.
        self.assertEqual(self._record(user=self.rep).status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(self._as(self.rep).get(self.url).status_code, status.HTTP_403_FORBIDDEN)

        # The manager (Lekki only) records at Lekki, not at Ikeja, and can't void.
        lekki_id = self._record(user=self.manager).data["id"]
        response = self._record(user=self.manager, location=str(self.ikeja.pk))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.data["code"], "LOCATION_ACCESS_DENIED")
        void = self._as(self.manager).post(f"{self.url}{lekki_id}/void/", {"reason": "x"}, format="json")
        self.assertEqual(void.status_code, status.HTTP_403_FORBIDDEN)

        # The owner's Ikeja expense is invisible to the manager.
        ikeja_id = self._record(location=str(self.ikeja.pk)).data["id"]
        listed = {row["id"] for row in self._as(self.manager).get(self.url).data["results"]}
        self.assertEqual(listed, {lekki_id})
        self.assertEqual(self.client.get(f"{self.url}{ikeja_id}/").status_code, status.HTTP_404_NOT_FOUND)

    def test_list_filters(self):
        self._record(category="fuel")
        self._record(category="rent", title="Shop rent", amount="200000.00")
        owner = self._as(self.owner)

        self.assertEqual(owner.get(self.url, {"category": "rent"}).data["count"], 1)
        self.assertEqual(owner.get(self.url, {"search": "rent"}).data["count"], 1)
        self.assertEqual(owner.get(self.url, {"location": str(self.ikeja.pk)}).data["count"], 0)
        self.assertEqual(owner.get(self.url, {"date_from": "bad"}).status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(owner.get(self.url, {"category": "lunch"}).status_code, status.HTTP_400_BAD_REQUEST)
