"""
Stock adjustment review (approve / reject) and transfer guards.

Setup: Lekki has 30 units, Ikeja 70. `owner` sees every location, `manager`
(inventory manager) only Lekki, `staff` (inventory staff) can't approve.
"""

from __future__ import annotations

from django.contrib.admin.sites import AdminSite
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from core.applications.inventory.admin import StockAdjustmentRequestAdmin
from core.applications.inventory.models import Inventory
from core.applications.inventory.models import InventoryLedgerEntry
from core.applications.inventory.models import StockAdjustmentRequest
from core.applications.inventory.services import InsufficientStock
from core.applications.inventory.services import StockTransferInput
from core.applications.inventory.services import bulk_transfer_stock
from core.applications.inventory.services import single_transfer_stock
from core.applications.inventory.tests.factories import InventoryFactory
from core.applications.inventory.tests.factories import OrganizationFactory
from core.applications.inventory.tests.factories import ProductFactory
from core.applications.inventory.tests.factories import StockAdjustmentRequestFactory
from core.applications.notification.models import AuditLog
from core.applications.users.models import Membership
from core.applications.users.models import MembershipLocation
from core.applications.users.services import seed_default_roles
from core.applications.users.tests.factories import UserFactory
from core.applications.warehouse.models import Warehouse
from core.helper.enums import ApprovalStatusChoices


class _Base(TestCase):
    def setUp(self):
        self.org = OrganizationFactory()
        plan = self.org.subscription.plan
        plan.max_locations = None
        plan.max_transactions_per_month = None
        plan.save(update_fields=["max_locations", "max_transactions_per_month"])
        self.roles = seed_default_roles(self.org)

        self.lekki = Warehouse.objects.create(organization=self.org, name="Lekki", code="LEK", is_default=True)
        self.ikeja = Warehouse.objects.create(organization=self.org, name="Ikeja", code="IKJ")

        self.owner = UserFactory()
        Membership.objects.create(user=self.owner, organization=self.org, role=self.roles["owner"])
        self.manager = UserFactory()
        manager_membership = Membership.objects.create(
            user=self.manager, organization=self.org, role=self.roles["inventory-manager"], all_locations=False
        )
        MembershipLocation.objects.create(membership=manager_membership, location=self.lekki)
        self.staff = UserFactory()
        Membership.objects.create(user=self.staff, organization=self.org, role=self.roles["inventory-staff"])

        self.product = ProductFactory(organization=self.org, name="Coca-Cola 50cl")
        InventoryFactory(product=self.product, warehouse=self.lekki, quantity=30)
        InventoryFactory(product=self.product, warehouse=self.ikeja, quantity=70)

        self.client = APIClient()
        self.base = f"/api/organizations/{self.org.pk}/inventory"

    def _as(self, user):
        self.client.force_authenticate(user)
        return self.client

    def _pending(self, warehouse=None, change=-5):
        return StockAdjustmentRequestFactory(
            product=self.product,
            warehouse=warehouse or self.lekki,
            requested_quantity_change=change,
            requested_by=self.staff,
            status=ApprovalStatusChoices.PENDING,
        )

    def _qty(self, warehouse):
        return Inventory.objects.get(product=self.product, warehouse=warehouse, variant=None, batch=None).quantity


class RejectAdjustmentTests(_Base):
    def _reject(self, adjustment, body, user=None):
        return self._as(user or self.owner).post(
            f"{self.base}/adjustments/{adjustment.pk}/reject/", body, format="json"
        )

    def test_reject_sets_status_and_never_touches_stock(self):
        adjustment = self._pending()
        ledger_before = InventoryLedgerEntry.objects.count()

        response = self._reject(adjustment, {"reason": "  Recount found them.  "})

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data, {"status": "rejected"})
        adjustment.refresh_from_db()
        self.assertEqual(adjustment.status, ApprovalStatusChoices.REJECTED)
        self.assertEqual(adjustment.rejection_reason, "Recount found them.")
        self.assertEqual(adjustment.reviewed_by, self.owner)
        self.assertIsNotNone(adjustment.reviewed_at)
        self.assertEqual(self._qty(self.lekki), 30)
        self.assertEqual(InventoryLedgerEntry.objects.count(), ledger_before)
        self.assertTrue(AuditLog.objects.filter(action="inventory.adjustment_rejected").exists())

    def test_reason_is_required(self):
        adjustment = self._pending()
        for body in ({}, {"reason": ""}, {"reason": "   "}):
            response = self._reject(adjustment, body)
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, body)
            self.assertEqual(response.data["code"], "VALIDATION_ERROR")
            self.assertIn("reason", response.data["errors"])
        adjustment.refresh_from_db()
        self.assertEqual(adjustment.status, ApprovalStatusChoices.PENDING)

    def test_only_pending_can_be_rejected(self):
        adjustment = self._pending()
        self._reject(adjustment, {"reason": "No"})
        response = self._reject(adjustment, {"reason": "Again"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["code"], "APPROVAL_ERROR")

    def test_rejected_request_cannot_then_be_approved(self):
        adjustment = self._pending()
        self._reject(adjustment, {"reason": "No"})
        response = self._as(self.owner).post(f"{self.base}/adjustments/{adjustment.pk}/approve/")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self._qty(self.lekki), 30)

    def test_needs_approve_permission(self):
        response = self._reject(self._pending(), {"reason": "No"}, user=self.staff)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_location_limited_reviewer_gets_404_outside_their_locations(self):
        at_ikeja = self._pending(warehouse=self.ikeja)
        response = self._reject(at_ikeja, {"reason": "No"}, user=self.manager)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

        at_lekki = self._pending(warehouse=self.lekki)
        response = self._reject(at_lekki, {"reason": "No"}, user=self.manager)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_other_organization_is_404(self):
        other_org = OrganizationFactory()
        other_wh = Warehouse.objects.create(organization=other_org, name="X", code="X")
        foreign = StockAdjustmentRequestFactory(
            product=ProductFactory(organization=other_org), warehouse=other_wh, status=ApprovalStatusChoices.PENDING
        )
        response = self._reject(foreign, {"reason": "No"})
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class ApproveAdjustmentTests(_Base):
    def test_approve_needs_no_body(self):
        adjustment = self._pending(change=-5)
        response = self._as(self.owner).post(f"{self.base}/adjustments/{adjustment.pk}/approve/")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data, {"status": "approved"})
        self.assertEqual(self._qty(self.lekki), 25)


class AdminReviewActionsTests(_Base):
    """The admin actions must go through the services (they used to only flip status)."""

    def _request(self):
        request = RequestFactory().post("/")
        request.user = self.owner
        request.session = {}
        request._messages = FallbackStorage(request)
        return request

    def test_admin_approve_moves_stock_and_writes_ledger(self):
        adjustment = self._pending(change=-5)
        ledger_before = InventoryLedgerEntry.objects.count()
        admin = StockAdjustmentRequestAdmin(StockAdjustmentRequest, AdminSite())

        admin.approve_requests(self._request(), StockAdjustmentRequest.objects.filter(pk=adjustment.pk))

        adjustment.refresh_from_db()
        self.assertEqual(adjustment.status, ApprovalStatusChoices.APPROVED)
        self.assertEqual(self._qty(self.lekki), 25)
        self.assertEqual(InventoryLedgerEntry.objects.count(), ledger_before + 1)

    def test_admin_reject_uses_stored_reason_and_keeps_stock(self):
        adjustment = self._pending(change=-5)
        StockAdjustmentRequest.objects.filter(pk=adjustment.pk).update(rejection_reason="Duplicate")
        admin = StockAdjustmentRequestAdmin(StockAdjustmentRequest, AdminSite())

        admin.reject_requests(self._request(), StockAdjustmentRequest.objects.filter(pk=adjustment.pk))

        adjustment.refresh_from_db()
        self.assertEqual(adjustment.status, ApprovalStatusChoices.REJECTED)
        self.assertEqual(self._qty(self.lekki), 30)


class TransferGuardTests(_Base):
    def _transfer(self, quantity, source=None, destination=None):
        return self._as(self.owner).post(
            f"{self.base}/transfers/execute/",
            {
                "product_id": str(self.product.pk),
                "source_warehouse_id": str((source or self.lekki).pk),
                "destination_warehouse_id": str((destination or self.ikeja).pk),
                "quantity": quantity,
            },
            format="json",
        )

    def test_transfer_moves_stock(self):
        response = self._transfer(10)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data, {"status": "transfer completed"})
        self.assertEqual(self._qty(self.lekki), 20)
        self.assertEqual(self._qty(self.ikeja), 80)

    def test_more_than_available_is_400_and_nothing_changes(self):
        ledger_before = InventoryLedgerEntry.objects.count()
        response = self._transfer(31)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["code"], "TRANSFER_ERROR")
        self.assertIn("available 30", response.data["detail"])
        self.assertEqual(self._qty(self.lekki), 30)
        self.assertEqual(self._qty(self.ikeja), 70)
        self.assertEqual(InventoryLedgerEntry.objects.count(), ledger_before)

    def test_source_with_no_stock_row_is_400_not_500(self):
        empty = Warehouse.objects.create(organization=self.org, name="Ajah", code="AJH")
        response = self._transfer(1, source=empty)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["code"], "TRANSFER_ERROR")

    def test_same_source_and_destination_is_400(self):
        response = self._transfer(1, destination=self.lekki)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["code"], "TRANSFER_ERROR")
        self.assertEqual(self._qty(self.lekki), 30)

    def test_service_rejects_insufficient_stock_directly(self):
        with self.assertRaises(InsufficientStock):
            single_transfer_stock(
                StockTransferInput(
                    source_warehouse=self.lekki, destination_warehouse=self.ikeja, product=self.product,
                    variant=None, batch=None, quantity=500,
                ),
                user=self.owner,
            )
        self.assertEqual(self._qty(self.lekki), 30)

    def test_bulk_transfer_is_all_or_nothing(self):
        other = ProductFactory(organization=self.org)
        InventoryFactory(product=other, warehouse=self.lekki, quantity=5)
        with self.assertRaises(InsufficientStock):
            bulk_transfer_stock(
                self.lekki,
                self.ikeja,
                [{"product": self.product, "quantity": 10}, {"product": other, "quantity": 6}],
                self.owner,
            )
        # The first item's move was rolled back with the failing second one.
        self.assertEqual(self._qty(self.lekki), 30)
        self.assertEqual(self._qty(self.ikeja), 70)
