"""
get_purchase_summary runs in the database. These tests pin it to the model
properties it replaced (Purchase.ordered_value / total_value / balance_due), so
the KPI cards can never drift from the numbers on each purchase.
"""

from __future__ import annotations

from decimal import Decimal

from django.test import TestCase

from core.applications.invoice.models import PurchaseInvoice
from core.applications.inventory.tests.factories import OrganizationFactory
from core.applications.inventory.tests.factories import ProductFactory
from core.applications.inventory.tests.factories import WarehouseFactory
from core.applications.purchase.models import Purchase
from core.applications.purchase.models import PurchaseItem
from core.applications.purchase.services import get_purchase_summary
from core.applications.vendors.models import Vendor
from core.helper.enums import InvoiceStatusChoices
from core.helper.enums import PurchasePaymentStatusChoices as PayStatus
from core.helper.enums import PurchaseStatusChoices as Status

CENT = Decimal("0.01")


def _reference_summary(purchases):
    """The original in-Python algorithm, kept here as the specification."""
    out = {
        "total_purchase_orders": len(purchases),
        "pending_delivery_count": 0,
        "partially_received_count": 0,
        "received_count": 0,
        "cancelled_count": 0,
        "unpaid_orders_count": 0,
        "partially_paid_orders_count": 0,
        "paid_orders_count": 0,
        "total_spend": Decimal(0),
        "total_ordered_value": Decimal(0),
        "total_paid": Decimal(0),
        "total_payable_balance": Decimal(0),
    }
    for po in purchases:
        if po.status in (Status.DRAFT, Status.ORDERED):
            out["pending_delivery_count"] += 1
        elif po.status == Status.PARTIALLY_RECEIVED:
            out["partially_received_count"] += 1
        elif po.status == Status.RECEIVED:
            out["received_count"] += 1
        elif po.status == Status.CANCELLED:
            out["cancelled_count"] += 1
        if po.status == Status.CANCELLED:
            continue
        key = {
            PayStatus.UNPAID: "unpaid_orders_count",
            PayStatus.PARTIALLY_PAID: "partially_paid_orders_count",
            PayStatus.PAID: "paid_orders_count",
        }.get(po.payment_status)
        if key:
            out[key] += 1
        out["total_ordered_value"] += Decimal(str(po.ordered_value))
        if any(item.quantity_received > 0 for item in po.items.all()):
            out["total_spend"] += Decimal(str(po.total_value))
        out["total_paid"] += Decimal(str(po.amount_paid or 0))
        out["total_payable_balance"] += po.balance_due
    for key in ("total_spend", "total_ordered_value", "total_paid", "total_payable_balance"):
        out[key] = out[key].quantize(CENT)
    return out


class PurchaseSummaryParityTests(TestCase):
    def setUp(self):
        self.org = OrganizationFactory()
        self.warehouse = WarehouseFactory(organization=self.org)
        self.vendor = Vendor.objects.create(organization=self.org, company_name="Acme Supplies")
        self.product = ProductFactory(organization=self.org)

    def _purchase(self, *, status, payment_status, paid="0", discount="0", other_costs="0", lines=()):
        purchase = Purchase.objects.create(
            organization=self.org,
            vendor=self.vendor,
            warehouse=self.warehouse,
            status=status,
            payment_status=payment_status,
            amount_paid=Decimal(paid),
            discount=Decimal(discount),
            other_costs=Decimal(other_costs),
        )
        for ordered, received, cost, line_discount in lines:
            PurchaseItem.objects.create(
                purchase=purchase,
                product=self.product,
                quantity_ordered=ordered,
                quantity_received=received,
                purchase_cost=Decimal(cost),
                discount=Decimal(line_discount),
            )
        return purchase

    def test_database_summary_matches_model_properties(self):
        # Draft, nothing received: payable is the ordered value.
        self._purchase(status=Status.DRAFT, payment_status=PayStatus.UNPAID, lines=[(10, 0, "100.00", "50.00")])
        # Partially received with a prorated line discount and header extras.
        self._purchase(
            status=Status.PARTIALLY_RECEIVED,
            payment_status=PayStatus.PARTIALLY_PAID,
            paid="200.00",
            discount="15.00",
            other_costs="40.00",
            lines=[(3, 1, "333.33", "10.00"), (7, 7, "12.50", "0.00")],
        )
        # Received and invoiced: the active (non-voided, newest) invoice total wins.
        invoiced = self._purchase(
            status=Status.RECEIVED, payment_status=PayStatus.PAID, paid="999.00", lines=[(5, 5, "200.00", "0.00")]
        )
        PurchaseInvoice.objects.create(
            organization=self.org, vendor=self.vendor, purchase=invoiced, warehouse=self.warehouse,
            invoice_number="PINV-OLD", status=InvoiceStatusChoices.CANCELLED, total=Decimal("1.00"),
        )
        PurchaseInvoice.objects.create(
            organization=self.org, vendor=self.vendor, purchase=invoiced, warehouse=self.warehouse,
            invoice_number="PINV-NEW", total=Decimal("1050.00"),
        )
        # A discount larger than the line value clamps at zero.
        self._purchase(status=Status.ORDERED, payment_status=PayStatus.UNPAID, lines=[(1, 1, "5.00", "9.00")])
        # Cancelled: counted, excluded from money totals.
        self._purchase(status=Status.CANCELLED, payment_status=PayStatus.UNPAID, lines=[(4, 0, "50.00", "0")])
        # No items at all.
        self._purchase(status=Status.ORDERED, payment_status=PayStatus.UNPAID, other_costs="25.00")

        purchases = list(
            Purchase.objects.for_organization(self.org).prefetch_related("items", "purchase_invoices")
        )

        self.assertEqual(get_purchase_summary(self.org), _reference_summary(purchases))

    def test_empty_organization(self):
        summary = get_purchase_summary(self.org)

        self.assertEqual(summary["total_purchase_orders"], 0)
        self.assertEqual(summary["total_payable_balance"], Decimal("0.00"))

    def test_query_count_is_constant(self):
        for _ in range(5):
            self._purchase(status=Status.ORDERED, payment_status=PayStatus.UNPAID, lines=[(1, 0, "10.00", "0")])

        with self.assertNumQueries(1):
            get_purchase_summary(self.org)
