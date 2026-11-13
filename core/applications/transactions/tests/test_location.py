"""Transactions carry the location they happened at (PRD §35) and are scoped by it."""

from __future__ import annotations

import importlib
import uuid

from django.apps import apps
from django.test import TestCase
from rest_framework.test import APIClient

from core.applications.inventory.models import Inventory
from core.applications.inventory.services import StockTransferInput
from core.applications.inventory.services import create_opening_stock
from core.applications.inventory.services import single_transfer_stock
from core.applications.inventory.tests.factories import OrganizationFactory
from core.applications.inventory.tests.factories import ProductFactory
from core.applications.transactions.models import Transaction
from core.applications.users.models import Membership
from core.applications.users.models import MembershipLocation
from core.applications.users.services import seed_default_roles
from core.applications.users.tests.factories import UserFactory
from core.applications.warehouse.models import Warehouse

backfill = importlib.import_module("core.applications.transactions.migrations.0005_backfill_transaction_location")


def _uid(value) -> str:
    return str(uuid.UUID(str(value)))


class TransactionLocationTests(TestCase):
    def setUp(self):
        self.org = OrganizationFactory()
        plan = self.org.subscription.plan
        plan.max_transactions_per_month = None
        plan.save(update_fields=["max_transactions_per_month"])
        self.roles = seed_default_roles(self.org)
        self.lekki = Warehouse.objects.create(organization=self.org, name="Lekki", code="LEK")
        self.ikeja = Warehouse.objects.create(organization=self.org, name="Ikeja", code="IKJ")
        self.owner = UserFactory()
        Membership.objects.create(user=self.owner, organization=self.org, role=self.roles["owner"])
        self.product = ProductFactory(organization=self.org)

    def _opening(self, warehouse, quantity=10):
        create_opening_stock(self.product, None, None, warehouse, quantity, self.owner)
        return Transaction.objects.filter(transaction_id__startswith="OPEN-").latest("created_at")

    def test_location_is_recorded_on_creation(self):
        trx = self._opening(self.lekki)

        self.assertEqual(_uid(trx.location_id), _uid(self.lekki.pk))

    def test_transfer_records_its_source_location(self):
        self._opening(self.lekki, 10)

        single_transfer_stock(
            StockTransferInput(
                source_warehouse=self.lekki, destination_warehouse=self.ikeja,
                product=self.product, variant=None, batch=None, quantity=4,
            ),
            user=self.owner,
        )

        trx = Transaction.objects.filter(transaction_id__startswith="TRF-").get()
        self.assertEqual(_uid(trx.location_id), _uid(self.lekki.pk))
        self.assertEqual(Inventory.objects.get(product=self.product, warehouse=self.ikeja).quantity, 4)

    def test_opening_stock_adds_instead_of_overwriting(self):
        self._opening(self.lekki, 10)
        self._opening(self.lekki, 5)  # e.g. a second opening entry: balance must follow the ledger

        self.assertEqual(Inventory.objects.get(product=self.product, warehouse=self.lekki).quantity, 15)

    def test_backfill_restores_missing_locations_and_is_rerunnable(self):
        trx = self._opening(self.ikeja)
        Transaction.objects.filter(pk=trx.pk).update(location=None)

        backfill.backfill_transaction_location(apps, None)
        backfill.backfill_transaction_location(apps, None)

        trx.refresh_from_db()
        self.assertEqual(_uid(trx.location_id), _uid(self.ikeja.pk))

    def test_api_scopes_transactions_to_member_locations(self):
        lekki_trx = self._opening(self.lekki)
        self._opening(self.ikeja)
        accountant = UserFactory()
        membership = Membership.objects.create(
            user=accountant, organization=self.org, role=self.roles["accountant"], all_locations=False
        )
        MembershipLocation.objects.create(membership=membership, location=self.lekki)
        client = APIClient()
        client.force_authenticate(accountant)

        response = client.get(f"/api/organizations/{self.org.pk}/transactions/")

        self.assertEqual(response.status_code, 200, response.content)
        rows = response.data.get("results", response.data) if isinstance(response.data, dict) else response.data
        self.assertEqual({_uid(row["id"]) for row in rows}, {_uid(lekki_trx.pk)})
