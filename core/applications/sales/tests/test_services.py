from decimal import Decimal
import uuid
from django.test import TestCase

from core.applications.customers.models import Customer, CustomerAddress
from core.applications.inventory.services import create_opening_stock
from core.applications.inventory.tests.factories import (
    OrganizationFactory,
    ProductFactory,
    StockLocationFactory,
    UserFactory,
)
from core.applications.invoice.models import Invoice
from core.applications.notification.audit.action import AuditAction
from core.applications.inventory.models import Inventory, InventoryLedgerEntry
from core.applications.notification.models import AuditLog
from core.applications.payment.models import InvoicePayment, Payment, Refund
from core.applications.sales.models import Sale, SaleItem, SaleReturn
from core.applications.sales.service import (
    cancel_sale,
    create_sale,
    create_sale_return,
    record_sale_audit_event,
    record_sale_return_audit_event,
    SaleCancellationError,
    SaleValidationError,
)
from core.applications.users.models import Membership
from core.applications.users.services import seed_default_roles
from core.helper.enums import (
    InvoiceStatusChoices,
    PaymentStatus,
    RestockActionChoices,
    SaleFulfillmentStatusChoices,
    SalePaymentStatusChoices,
)


class SalesServicesTest(TestCase):
    def setUp(self):
        self.org = OrganizationFactory()
        self.roles = seed_default_roles(self.org)
        self.user = UserFactory()
        self.membership = Membership.objects.create(
            user=self.user,
            organization=self.org,
            role=self.roles["owner"],
            is_active=True,
        )

        self.location = StockLocationFactory(warehouse__organization=self.org)
        self.product = ProductFactory(organization=self.org, customer_sale_price=Decimal("50.00"))
        self.customer = Customer.objects.create(
            organization=self.org,
            name="Alpha Corp",
            email="purchasing@alphacorp.test",
            phone="+2348011223344",
        )
        CustomerAddress.objects.create(
            customer=self.customer,
            street="15 Commercial Ave",
            city="Lagos",
            is_default=True,
        )

        # Seed initial inventory
        create_opening_stock(self.product, None, None, self.location.warehouse, 100, self.user)

    def test_create_sale_creates_real_invoice_and_items(self):
        """Creating a sale automatically provisions an Invoice and links lines."""
        sale_data = {
            "organization": self.org,
            "customer": self.customer,
            "location": self.location,
            "sales_rep": self.user,
            "items": [
                {
                    "product": self.product,
                    "product_name": self.product.name,
                    "quantity": 4,
                    "unit_price": Decimal("50.00"),
                }
            ],
        }
        sale = create_sale(sale_data)

        # 1. Sale integrity
        self.assertIsNotNone(sale.id)
        self.assertEqual(sale.total, Decimal("200.00"))

        # 2. Real Invoice created
        invoice = Invoice.objects.filter(sale=sale).first()
        self.assertIsNotNone(invoice)
        self.assertEqual(str(invoice.organization_id).replace("-", ""), str(self.org.id).replace("-", ""))
        self.assertEqual(str(invoice.warehouse_id).replace("-", ""), str(self.location.warehouse_id).replace("-", ""))
        self.assertEqual(invoice.sales_rep, self.user)
        self.assertEqual(invoice.total, Decimal("200.00"))
        self.assertEqual(str(invoice.customer_id).replace("-", ""), str(self.customer.id).replace("-", ""))

        # 3. Invoice lines created
        lines = invoice.items.all()
        self.assertEqual(lines.count(), 1)
        self.assertEqual(str(lines.first().product_id).replace("-", ""), str(self.product.id).replace("-", ""))
        self.assertEqual(lines.first().quantity, 4)
        self.assertEqual(lines.first().unit_price, Decimal("50.00"))

        # 4. Audit Log generated
        self.assertTrue(
            AuditLog.objects.filter(action=AuditAction.SALES_ORDER_CREATED, resource_id=str(sale.id)).exists()
        )

    def test_create_sale_paid_creates_payment_and_receipt(self):
        """Paid sale records Payment and official Receipt (InvoicePayment)."""
        sale_data = {
            "organization": self.org,
            "customer": self.customer,
            "location": self.location,
            "sales_rep": self.user,
            "payment": {"amount": Decimal("100.00"), "method": "cash"},
            "items": [
                {
                    "product": self.product,
                    "product_name": self.product.name,
                    "quantity": 2,
                    "unit_price": Decimal("50.00"),
                }
            ],
        }
        sale = create_sale(sale_data)

        # 1. Sale marked as PAID
        self.assertEqual(sale.payment_status, SalePaymentStatusChoices.PAID)

        # 2. Invoice marked as PAID
        invoice = Invoice.objects.get(sale=sale)
        self.assertEqual(invoice.status, InvoiceStatusChoices.PAID)
        self.assertEqual(invoice.amount_paid, Decimal("100.00"))

        # 3. Payment recorded
        payment = Payment.objects.filter(description__contains=sale.sale_id).first()
        self.assertIsNotNone(payment)
        self.assertEqual(payment.amount, Decimal("100.00"))
        self.assertEqual(payment.status, PaymentStatus.SUCCESS)

        # 4. Receipt (InvoicePayment) recorded
        receipt = InvoicePayment.objects.filter(invoice=invoice, payment=payment).first()
        self.assertIsNotNone(receipt)
        self.assertEqual(receipt.amount, Decimal("100.00"))
        self.assertEqual(receipt.balance_after, Decimal("0.00"))
        self.assertTrue(receipt.receipt_number.startswith("R-"))

    def test_create_sale_cash_overpayment_records_total_and_change_due(self):
        """Cash overpayment records net total as paid and computes change_due."""
        sale_data = {
            "organization": self.org,
            "customer": self.customer,
            "location": self.location,
            "discount_amount": Decimal("50.00"),
            "payment": {"amount": Decimal("1000.00"), "method": "cash"},
            "items": [
                {
                    "product": self.product,
                    "quantity": 10,
                    "unit_price": Decimal("100.00"),
                }
            ],
        }
        sale = create_sale(sale_data, actor=self.user)
        self.assertEqual(sale.total, Decimal("950.00"))
        self.assertEqual(sale.change_due, Decimal("50.00"))
        self.assertEqual(sale.payment_status, SalePaymentStatusChoices.PAID)

        invoice = Invoice.objects.get(sale=sale)
        self.assertEqual(invoice.amount_paid, Decimal("950.00"))
        self.assertEqual(invoice.status, InvoiceStatusChoices.PAID)

    def test_create_sale_payment_amount_omitted_defaults_to_full_balance(self):
        """Omitted payment.amount pays the full order balance automatically."""
        sale_data = {
            "organization": self.org,
            "customer": self.customer,
            "location": self.location,
            "discount_amount": Decimal("50.00"),
            "payment": {"method": "cash"},
            "items": [
                {
                    "product": self.product,
                    "quantity": 10,
                    "unit_price": Decimal("100.00"),
                }
            ],
        }
        sale = create_sale(sale_data, actor=self.user)
        self.assertEqual(sale.total, Decimal("950.00"))
        self.assertEqual(sale.change_due, Decimal("0.00"))
        self.assertEqual(sale.payment_status, SalePaymentStatusChoices.PAID)

        invoice = Invoice.objects.get(sale=sale)
        self.assertEqual(invoice.amount_paid, Decimal("950.00"))

    def test_create_sale_unpaid_does_not_create_payment_or_receipt(self):
        """Unpaid sale generates Invoice without Payment or Receipt."""
        sale_data = {
            "organization": self.org,
            "customer": self.customer,
            "location": self.location,
            "sales_rep": self.user,
            "items": [
                {
                    "product": self.product,
                    "product_name": self.product.name,
                    "quantity": 1,
                    "unit_price": Decimal("50.00"),
                }
            ],
        }
        sale = create_sale(sale_data)

        # Invoice exists
        self.assertTrue(Invoice.objects.filter(sale=sale).exists())

        # No Payment or Receipt
        self.assertFalse(Payment.objects.filter(description__contains=sale.sale_id).exists())
        self.assertFalse(InvoicePayment.objects.filter(invoice__sale=sale).exists())

    def test_create_sale_return_restocks_and_creates_refund_and_audit(self):
        """Sale return against a paid sale creates Refund, restocks stock, and dispatches audit log."""
        # 1. Create a paid sale first
        sale_data = {
            "organization": self.org,
            "customer": self.customer,
            "location": self.location,
            "sales_rep": self.user,
            "payment": {"amount": Decimal("250.00"), "method": "cash"},
            "items": [
                {
                    "product": self.product,
                    "product_name": self.product.name,
                    "quantity": 5,
                    "unit_price": Decimal("50.00"),
                }
            ],
        }
        sale = create_sale(sale_data)
        sale_item = sale.items.first()

        # 2. Process return of 2 items
        return_data = {
            "original_sale": sale,
            "organization": self.org,
            "return_reason": "Customer overbought",
            "refund_amount": Decimal("100.00"),
            "items": [
                {
                    "sale_item": sale_item,
                    "quantity": 2,
                    "refund_amount": Decimal("100.00"),
                    "restock_action": RestockActionChoices.RETURN_TO_STOCK,
                }
            ],
        }
        sale_return = create_sale_return(return_data)

        # 3. Return created and audited
        self.assertIsNotNone(sale_return.id)
        self.assertTrue(
            AuditLog.objects.filter(action=AuditAction.SALES_RETURN_CREATED, resource_id=str(sale_return.id)).exists()
        )

        # 4. Refund record created against original payment
        refund = Refund.objects.filter(amount=Decimal("100.00")).first()
        self.assertIsNotNone(refund)
        self.assertEqual(refund.status, PaymentStatus.SUCCESS)
        self.assertIn(str(sale_return.id)[:8], refund.reference)

    def test_returned_quantity_tracking_and_boundary_checks(self):
        """SaleItem.returned_quantity is tracked and bounds are enforced."""
        sale = create_sale({
            "organization": self.org,
            "customer": self.customer,
            "location": self.location,
            "sales_rep": self.user,
            "payment": {"amount": Decimal("250.00"), "method": "cash"},
            "items": [
                {
                    "product": self.product,
                    "product_name": self.product.name,
                    "quantity": 5,
                    "unit_price": Decimal("50.00"),
                }
            ],
        })
        sale_item = sale.items.first()
        self.assertEqual(sale_item.returned_quantity, 0)

        # First return: 2 items
        create_sale_return({
            "original_sale": sale,
            "organization": self.org,
            "return_reason": "First partial return",
            "items": [{"sale_item": sale_item, "quantity": 2, "refund_amount": Decimal("100.00")}],
        })
        sale_item.refresh_from_db()
        self.assertEqual(sale_item.returned_quantity, 2)

        # Second return: remaining 3 items
        create_sale_return({
            "original_sale": sale,
            "organization": self.org,
            "return_reason": "Final return",
            "items": [{"sale_item": sale_item, "quantity": 3, "refund_amount": Decimal("150.00")}],
        })
        sale_item.refresh_from_db()
        self.assertEqual(sale_item.returned_quantity, 5)

        # Sale marked as RETURNED
        sale.refresh_from_db()
        self.assertEqual(sale.fulfillment_status, SaleFulfillmentStatusChoices.RETURNED)

        # Third return: exceeding quantity must fail
        with self.assertRaises(SaleValidationError):
            create_sale_return({
                "original_sale": sale,
                "organization": self.org,
                "return_reason": "Over return",
                "items": [{"sale_item": sale_item, "quantity": 1, "refund_amount": Decimal("50.00")}],
            })

    def test_cancel_sale_restores_inventory_and_logs_audit(self):
        """Cancelling a sale restores stock, logs compensatory ledger entries and audit events."""
        inv_before = Inventory.objects.filter(
            product=self.product, warehouse=self.location.warehouse
        ).first().quantity

        sale = create_sale({
            "organization": self.org,
            "customer": self.customer,
            "location": self.location,
            "sales_rep": self.user,
            "items": [
                {
                    "product": self.product,
                    "product_name": self.product.name,
                    "quantity": 4,
                    "unit_price": Decimal("50.00"),
                }
            ],
        })

        inv_after_sale = Inventory.objects.filter(
            product=self.product, warehouse=self.location.warehouse
        ).first().quantity
        self.assertEqual(inv_after_sale, inv_before - 4)

        # Cancel sale
        cancelled_sale = cancel_sale(
            sale, actor=self.user, reason="Customer cancelled order prior to dispatch"
        )
        self.assertIsNotNone(cancelled_sale.cancelled_at)
        self.assertEqual(cancelled_sale.cancelled_by, self.user)
        self.assertEqual(cancelled_sale.cancel_reason, "Customer cancelled order prior to dispatch")
        self.assertEqual(cancelled_sale.fulfillment_status, SaleFulfillmentStatusChoices.CANCELED)

        # Inventory fully restored
        inv_restored = Inventory.objects.filter(
            product=self.product, warehouse=self.location.warehouse
        ).first().quantity
        self.assertEqual(inv_restored, inv_before)

        # Compensatory ledger entry created
        ledger_entry = InventoryLedgerEntry.objects.filter(
            product=self.product,
            quantity_moved=4,
            transaction__transaction_id__startswith=f"CNL-{sale.sale_id}"[:50],
        ).first()
        self.assertIsNotNone(ledger_entry)

        # Cancellation audit log recorded
        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditAction.SALES_ORDER_CANCELLED, resource_id=str(sale.id)
            ).exists()
        )

    def test_cancel_already_cancelled_sale_fails(self):
        """Cancelling an already cancelled sale raises SaleCancellationError."""
        sale = create_sale({
            "organization": self.org,
            "customer": self.customer,
            "location": self.location,
            "sales_rep": self.user,
            "items": [
                {
                    "product": self.product,
                    "product_name": self.product.name,
                    "quantity": 2,
                    "unit_price": Decimal("50.00"),
                }
            ],
        })
        cancel_sale(sale, actor=self.user, reason="First cancel")

        with self.assertRaises(SaleCancellationError):
            cancel_sale(sale, actor=self.user, reason="Second cancel")

    def test_cancel_sale_with_returns_fails(self):
        """Cancelling a sale with existing returns raises SaleCancellationError."""
        sale = create_sale({
            "organization": self.org,
            "customer": self.customer,
            "location": self.location,
            "sales_rep": self.user,
            "payment": {"amount": Decimal("100.00"), "method": "cash"},
            "items": [
                {
                    "product": self.product,
                    "product_name": self.product.name,
                    "quantity": 2,
                    "unit_price": Decimal("50.00"),
                }
            ],
        })
        create_sale_return({
            "original_sale": sale,
            "organization": self.org,
            "return_reason": "Damaged unit",
            "items": [
                {"sale_item": sale.items.first(), "quantity": 1, "refund_amount": Decimal("50.00")}
            ],
        })

        with self.assertRaises(SaleCancellationError):
            cancel_sale(sale, actor=self.user, reason="Attempt cancellation after return")

    def test_create_sale_without_location_defaults_to_active_warehouse(self):
        """When location is omitted, sale automatically falls back to organization's active warehouse."""
        sale = create_sale({
            "organization": self.org,
            "customer": self.customer,
            "sales_rep": self.user,
            "items": [
                {
                    "product": self.product,
                    "quantity": 2,
                    "unit_price": Decimal("50.00"),
                }
            ],
        })
        self.assertIsNotNone(sale.id)
        self.assertEqual(str(sale.warehouse_id).replace("-", ""), str(self.location.warehouse_id).replace("-", ""))
        self.assertEqual(sale.total, Decimal("100.00"))

    def test_create_sale_with_explicit_warehouse_and_no_location(self):
        """When warehouse is explicitly passed without location, stock is deducted from that warehouse."""
        sale = create_sale({
            "organization": self.org,
            "customer": self.customer,
            "warehouse": self.location.warehouse,
            "sales_rep": self.user,
            "items": [
                {
                    "product": self.product,
                    "quantity": 1,
                    "unit_price": Decimal("50.00"),
                }
            ],
        })
        self.assertIsNotNone(sale.id)
        self.assertEqual(str(sale.warehouse_id).replace("-", ""), str(self.location.warehouse_id).replace("-", ""))
        self.assertEqual(sale.total, Decimal("50.00"))


