from datetime import date
from decimal import Decimal

from django.test import TestCase

from core.applications.inventory.models import Inventory, InventoryLedgerEntry
from core.applications.inventory.tests.factories import (
    OrganizationFactory,
    ProductFactory,
    UserFactory,
    WarehouseFactory,
)
from core.applications.payment.exceptions import PaymentValidationError
from core.applications.products.models import ProductBatch, ProductUnitConversion
from core.applications.purchase.exceptions import (
    CannotDeleteReceivedPurchaseError,
    PurchaseCancellationError,
    PurchaseReceiveError,
    PurchaseReturnError,
)
from core.applications.purchase.services import (
    PurchaseInvoiceInput,
    PurchasePaymentInput,
    cancel_purchase_order,
    create_purchase_order,
    create_purchase_return,
    delete_purchase_order,
    get_purchase_available_actions,
    get_purchase_summary,
    receive_purchase_order,
    record_purchase_invoice,
    record_supplier_payment,
)
from core.applications.transactions.models import FinancialLedgerEntry, Transaction
from core.applications.vendors.models import Vendor
from core.applications.vendors.services import get_vendor_balance_summary
from core.helper.enums import (
    AccountNameChoices,
    InvoiceStatusChoices,
    PaymentMethod,
    PurchasePaymentStatusChoices,
    PurchaseStatusChoices,
    TransactionTypeChoices,
)


class PurchaseServiceTests(TestCase):
    def setUp(self):
        self.org = OrganizationFactory()
        self.user = UserFactory()
        self.warehouse = WarehouseFactory(organization=self.org)
        self.vendor = Vendor.objects.create(
            organization=self.org,
            company_name="Lagos Industrial Supplies",
            contact_person="Chinedu Okafor",
            email="sales@lagosindustrial.ng",
            phone="+2348001112222",
            address="12 Wharf Road, Apapa, Lagos",
        )
        self.product = ProductFactory(
            organization=self.org,
            name="Industrial Valve",
            purchase_cost=Decimal("100.00"),
        )
        self.carton_unit = ProductUnitConversion.objects.create(
            product=self.product,
            unit_name="carton",
            multiplier=Decimal("12.000"),
        )

    def test_receive_purchase_order_applies_unit_multiplier_batch_and_double_entry_ledger(self):
        purchase = create_purchase_order(
            organization=self.org,
            ordered_by=self.user,
            vendor=self.vendor,
            warehouse=self.warehouse,
            items_data=[
                {
                    "product": self.product,
                    "unit": self.carton_unit,
                    "quantity_ordered": 5,
                    "purchase_cost": Decimal("1200.00"),
                    "discount": Decimal("0.00"),
                }
            ],
        )
        item = purchase.items.first()

        receive_purchase_order(
            purchase=purchase,
            items_received=[
                {
                    "item_id": item.pk,
                    "quantity_received": 2,
                    "batch_number": "BATCH-VALVE-01",
                    "expiry_date": date(2028, 12, 31),
                }
            ],
            user=self.user,
        )

        purchase.refresh_from_db()
        item.refresh_from_db()
        self.assertEqual(purchase.status, PurchaseStatusChoices.PARTIALLY_RECEIVED)
        self.assertEqual(item.quantity_received, 2)

        batch = ProductBatch.objects.get(product=self.product, batch_number="BATCH-VALVE-01")
        self.assertEqual(batch.expiry_date, date(2028, 12, 31))

        inv = Inventory.objects.get(
            product=self.product,
            warehouse=self.warehouse,
            batch=batch,
        )
        self.assertEqual(inv.quantity, 24)

        ledger_entry = InventoryLedgerEntry.objects.filter(
            product=self.product,
            warehouse=self.warehouse,
            batch=batch,
        ).first()
        self.assertIsNotNone(ledger_entry)
        self.assertEqual(ledger_entry.quantity_moved, 24)
        self.assertEqual(ledger_entry.unit_cost, Decimal("100.00"))

        fin_entries = list(
            FinancialLedgerEntry.objects.filter(transaction=ledger_entry.transaction)
        )
        self.assertEqual(len(fin_entries), 2)
        debit_entry = next(e for e in fin_entries if e.debit > Decimal("0.00"))
        credit_entry = next(e for e in fin_entries if e.credit > Decimal("0.00"))
        self.assertEqual(debit_entry.account_name, AccountNameChoices.INVENTORY_ASSET)
        self.assertEqual(debit_entry.debit, Decimal("2400.00"))
        self.assertEqual(credit_entry.account_name, AccountNameChoices.ACCOUNTS_PAYABLE)
        self.assertEqual(credit_entry.credit, Decimal("2400.00"))

    def test_record_purchase_invoice_and_supplier_payment_updates_status_and_ledger(self):
        purchase = create_purchase_order(
            organization=self.org,
            ordered_by=self.user,
            vendor=self.vendor,
            warehouse=self.warehouse,
            other_costs=Decimal("100.00"),
            discount=Decimal("50.00"),
            items_data=[
                {
                    "product": self.product,
                    "quantity_ordered": 10,
                    "purchase_cost": Decimal("100.00"),
                    "discount": Decimal("0.00"),
                }
            ],
        )
        item = purchase.items.first()

        receive_purchase_order(
            purchase=purchase,
            items_received=[{"item_id": item.pk, "quantity_received": 10}],
            user=self.user,
        )

        purchase_invoice = record_purchase_invoice(
            purchase=purchase,
            actor=self.user,
            payload=PurchaseInvoiceInput(
                vendor_invoice_number="SUP-INV-2026-099",
                billed_to="RIINOX HQ",
                issue_date=date(2026, 10, 10),
                due_date=date(2026, 11, 10),
                tax_rate=Decimal("10.00"),
            ),
        )
        self.assertEqual(purchase_invoice.subtotal, Decimal("1000.00"))
        self.assertEqual(purchase_invoice.tax_amount, Decimal("95.00"))
        self.assertEqual(purchase_invoice.total, Decimal("1145.00"))
        self.assertEqual(purchase_invoice.status, InvoiceStatusChoices.CONFIRMED)

        pp1 = record_supplier_payment(
            purchase=purchase,
            actor=self.user,
            payload=PurchasePaymentInput(
                amount=Decimal("500.00"),
                method=PaymentMethod.BANK_TRANSFER,
                payment_date=date(2026, 10, 12),
                notes="First installment",
            ),
        )
        purchase.refresh_from_db()
        purchase_invoice.refresh_from_db()
        self.assertEqual(purchase.amount_paid, Decimal("500.00"))
        self.assertEqual(purchase.payment_status, PurchasePaymentStatusChoices.PARTIALLY_PAID)
        self.assertEqual(purchase_invoice.amount_paid, Decimal("500.00"))
        self.assertEqual(purchase_invoice.status, InvoiceStatusChoices.PARTIALLY_PAID)
        self.assertEqual(pp1.balance_after, Decimal("645.00"))

        pp2 = record_supplier_payment(
            purchase=purchase,
            actor=self.user,
            payload=PurchasePaymentInput(
                amount=None,
                method=PaymentMethod.CASH,
            ),
        )
        purchase.refresh_from_db()
        purchase_invoice.refresh_from_db()
        self.assertEqual(pp2.amount, Decimal("645.00"))
        self.assertEqual(pp2.balance_after, Decimal("0.00"))
        self.assertEqual(purchase.amount_paid, Decimal("1145.00"))
        self.assertEqual(purchase.payment_status, PurchasePaymentStatusChoices.PAID)
        self.assertEqual(purchase_invoice.status, InvoiceStatusChoices.PAID)

        payment_txns = Transaction.objects.filter(
            organization=self.org,
            transaction_type=TransactionTypeChoices.PAYMENT_SENT,
        )
        self.assertEqual(payment_txns.count(), 2)

        with self.assertRaises(PaymentValidationError):
            record_supplier_payment(
                purchase=purchase,
                actor=self.user,
                payload=PurchasePaymentInput(
                    amount=Decimal("10.00"),
                    method=PaymentMethod.CASH,
                ),
            )

    def test_cancel_and_delete_purchase_order_rules(self):
        po = create_purchase_order(
            organization=self.org,
            ordered_by=self.user,
            vendor=self.vendor,
            warehouse=self.warehouse,
            items_data=[
                {
                    "product": self.product,
                    "quantity_ordered": 10,
                    "purchase_cost": Decimal("100.00"),
                }
            ],
        )
        cancelled_po = cancel_purchase_order(
            purchase=po,
            actor=self.user,
            reason="Supplier out of stock",
        )
        self.assertEqual(cancelled_po.status, PurchaseStatusChoices.CANCELLED)
        self.assertEqual(cancelled_po.cancel_reason, "Supplier out of stock")

        with self.assertRaises(PurchaseReceiveError):
            receive_purchase_order(
                purchase=cancelled_po,
                items_received=[{"item_id": cancelled_po.items.first().pk, "quantity_received": 5}],
                user=self.user,
            )

        po2 = create_purchase_order(
            organization=self.org,
            ordered_by=self.user,
            vendor=self.vendor,
            warehouse=self.warehouse,
            items_data=[
                {
                    "product": self.product,
                    "quantity_ordered": 10,
                    "purchase_cost": Decimal("100.00"),
                }
            ],
        )
        receive_purchase_order(
            purchase=po2,
            items_received=[{"item_id": po2.items.first().pk, "quantity_received": 4}],
            user=self.user,
        )
        with self.assertRaises(PurchaseCancellationError):
            cancel_purchase_order(purchase=po2, actor=self.user, reason="Too late")
        with self.assertRaises(CannotDeleteReceivedPurchaseError):
            delete_purchase_order(purchase=po2, actor=self.user)

    def test_create_purchase_return_deducts_stock_and_posts_financial_entries(self):
        po = create_purchase_order(
            organization=self.org,
            ordered_by=self.user,
            vendor=self.vendor,
            warehouse=self.warehouse,
            items_data=[
                {
                    "product": self.product,
                    "quantity_ordered": 10,
                    "purchase_cost": Decimal("100.00"),
                }
            ],
        )
        item = po.items.first()
        receive_purchase_order(
            purchase=po,
            items_received=[{"item_id": item.pk, "quantity_received": 10}],
            user=self.user,
        )

        purchase_return = create_purchase_return(
            purchase=po,
            actor=self.user,
            reason="Defective valves",
            items=[{"purchase_item": item.pk, "quantity": 3}],
        )
        self.assertEqual(purchase_return.total_refund_amount, Decimal("300.00"))

        inv = Inventory.objects.get(product=self.product, warehouse=self.warehouse)
        self.assertEqual(inv.quantity, 7)

        with self.assertRaises(PurchaseReturnError):
            create_purchase_return(
                purchase=po,
                actor=self.user,
                reason="Excess return",
                items=[{"purchase_item": item.pk, "quantity": 8}],
            )

        summary = get_purchase_summary(self.org)
        self.assertEqual(summary["total_purchase_orders"], 1)
        self.assertEqual(summary["received_count"], 1)
        self.assertIn("create_return", get_purchase_available_actions(po))

        vendor_summary = get_vendor_balance_summary(self.vendor)
        self.assertEqual(vendor_summary["total_purchases"], Decimal("1000.00"))
        self.assertEqual(vendor_summary["amount_paid"], Decimal("0.00"))
        self.assertEqual(vendor_summary["outstanding_balance"], Decimal("1000.00"))

