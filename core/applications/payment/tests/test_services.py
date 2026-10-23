from decimal import Decimal
import uuid
from django.test import TestCase
from django.utils import timezone

from core.applications.customers.models import Customer
from core.applications.inventory.tests.factories import (
    OrganizationFactory,
    StockLocationFactory,
    UserFactory,
)
from core.applications.invoice.models import Invoice
from core.applications.payment.exceptions import PaymentValidationError
from core.applications.payment.models import InvoicePayment, Payment, Refund
from core.applications.payment.services import (
    parse_payment,
    payment_status,
    record_invoice_payment,
    record_refund_for_return,
)
from core.applications.sales.models import Sale, SaleReturn
from core.helper.enums import (
    InvoiceStatusChoices,
    PaymentMethod,
    PaymentStatus,
    SalePaymentStatusChoices,
)


class PaymentServicesTest(TestCase):
    def setUp(self):
        self.org = OrganizationFactory()
        self.user = UserFactory()
        self.location = StockLocationFactory(warehouse__organization=self.org)
        self.customer = Customer.objects.create(
            organization=self.org,
            name="Test Customer",
            email="cust@test.com",
        )
        self.invoice = Invoice.objects.create(
            organization=self.org,
            customer=self.customer,
            warehouse=self.location.warehouse,
            sales_rep=self.user,
            invoice_number=f"INV-{uuid.uuid4().hex[:6].upper()}",
            due_date=timezone.localdate(),
            status=InvoiceStatusChoices.CONFIRMED,
            subtotal=Decimal("100.00"),
            total=Decimal("100.00"),
            amount_paid=Decimal("0.00"),
        )

    def test_parse_payment_valid(self):
        data = {"amount": "50.00", "method": PaymentMethod.CASH, "reference": "REF123"}
        amount, method, ref = parse_payment(data, Decimal("100.00"))
        self.assertEqual(amount, Decimal("50.00"))
        self.assertEqual(method, PaymentMethod.CASH.value)
        self.assertEqual(ref, "REF123")

    def test_parse_payment_none_or_empty(self):
        amount, method, ref = parse_payment(None, Decimal("100.00"))
        self.assertEqual(amount, Decimal("0.00"))
        self.assertEqual(method, "")
        self.assertIsNone(ref)

    def test_parse_payment_cash_overpayment_calculates_change(self):
        data = {"amount": "150.00", "method": PaymentMethod.CASH}
        res = parse_payment(data, Decimal("100.00"))
        self.assertEqual(res.amount, Decimal("100.00"))
        self.assertEqual(res.change_due, Decimal("50.00"))

    def test_parse_payment_card_overpayment_rejected(self):
        data = {"amount": "150.00", "method": PaymentMethod.CREDIT_CARD}
        with self.assertRaises(PaymentValidationError):
            parse_payment(data, Decimal("100.00"))

    def test_parse_payment_amount_omitted_defaults_to_full_balance(self):
        data = {"method": PaymentMethod.CASH}
        res = parse_payment(data, Decimal("100.00"))
        self.assertEqual(res.amount, Decimal("100.00"))
        self.assertEqual(res.change_due, Decimal("0.00"))

    def test_parse_payment_invalid_method(self):
        data = {"amount": "50.00", "method": "invalid_method"}
        with self.assertRaises(PaymentValidationError):
            parse_payment(data, Decimal("100.00"))

    def test_payment_status(self):
        self.assertEqual(payment_status(Decimal("100.00"), Decimal("100.00")), SalePaymentStatusChoices.PAID)
        self.assertEqual(payment_status(Decimal("100.00"), Decimal("50.00")), SalePaymentStatusChoices.PARTIALLY_PAID)
        self.assertEqual(payment_status(Decimal("100.00"), Decimal("0.00")), SalePaymentStatusChoices.UNPAID)

    def test_record_invoice_payment_partial_and_full(self):
        # 1. Partial payment of 40
        payment, receipt = record_invoice_payment(
            self.invoice,
            amount=Decimal("40.00"),
            method=PaymentMethod.BANK_TRANSFER,
            actor=self.user,
            currency="USD",
        )
        self.invoice.refresh_from_db()
        self.customer.refresh_from_db()

        self.assertEqual(payment.amount, Decimal("40.00"))
        self.assertEqual(payment.status, PaymentStatus.SUCCESS)
        self.assertEqual(receipt.amount, Decimal("40.00"))
        self.assertEqual(receipt.balance_after, Decimal("60.00"))
        self.assertEqual(self.invoice.amount_paid, Decimal("40.00"))
        self.assertEqual(self.invoice.status, InvoiceStatusChoices.PARTIALLY_PAID)
        self.assertEqual(self.customer.balance, Decimal("60.00"))

        # 2. Complete payment of remaining 60
        payment2, receipt2 = record_invoice_payment(
            self.invoice,
            amount=Decimal("60.00"),
            method=PaymentMethod.CASH,
            actor=self.user,
            currency="USD",
        )
        self.invoice.refresh_from_db()
        self.customer.refresh_from_db()

        self.assertEqual(self.invoice.amount_paid, Decimal("100.00"))
        self.assertEqual(self.invoice.status, InvoiceStatusChoices.PAID)
        self.assertEqual(receipt2.balance_after, Decimal("0.00"))
        self.assertEqual(self.customer.balance, Decimal("0.00"))

    def test_record_invoice_payment_non_cash_exceeding_balance_fails(self):
        with self.assertRaises(PaymentValidationError):
            record_invoice_payment(
                self.invoice,
                amount=Decimal("150.00"),
                method=PaymentMethod.CREDIT_CARD,
                actor=self.user,
                currency="USD",
            )

    def test_record_invoice_payment_cash_overpayment_returns_change(self):
        res = record_invoice_payment(
            self.invoice,
            amount=Decimal("150.00"),
            method=PaymentMethod.CASH,
            actor=self.user,
            currency="USD",
        )
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.amount_paid, Decimal("100.00"))
        self.assertEqual(res.change_due, Decimal("50.00"))

    def test_record_invoice_payment_invalid_status_fails(self):
        self.invoice.status = InvoiceStatusChoices.DRAFT
        self.invoice.save(update_fields=["status"])
        with self.assertRaises(PaymentValidationError):
            record_invoice_payment(
                self.invoice,
                amount=Decimal("50.00"),
                method=PaymentMethod.CASH,
                actor=self.user,
                currency="USD",
            )

    def test_record_refund_for_return(self):
        sale = Sale.objects.create(
            sale_id=f"SALE-{uuid.uuid4().hex[:6]}",
            organization=self.org,
            customer=self.customer,
            location=self.location,
            sales_rep=self.user,
            subtotal=Decimal("100.00"),
            total=Decimal("100.00"),
        )
        self.invoice.sale = sale
        self.invoice.save(update_fields=["sale"])

        # Pay invoice
        payment, receipt = record_invoice_payment(
            self.invoice,
            amount=Decimal("100.00"),
            method=PaymentMethod.CASH,
            actor=self.user,
            currency="USD",
        )

        sale_return = SaleReturn.objects.create(
            original_sale=sale,
            organization=self.org,
            return_reason="Damaged item",
            refund_amount=Decimal("30.00"),
        )

        refund = record_refund_for_return(sale_return)
        self.assertIsNotNone(refund)
        self.assertEqual(str(refund.payment_id).replace("-", ""), str(payment.id).replace("-", ""))
        self.assertEqual(refund.status, PaymentStatus.SUCCESS)

    def test_record_refund_exceeding_payment_fails(self):
        sale = Sale.objects.create(
            sale_id=f"SALE-{uuid.uuid4().hex[:6]}",
            organization=self.org,
            customer=self.customer,
            location=self.location,
            sales_rep=self.user,
            subtotal=Decimal("100.00"),
            total=Decimal("100.00"),
        )
        self.invoice.sale = sale
        self.invoice.save(update_fields=["sale"])

        record_invoice_payment(
            self.invoice,
            amount=Decimal("50.00"),
            method=PaymentMethod.CASH,
            actor=self.user,
            currency="USD",
        )

        sale_return = SaleReturn.objects.create(
            original_sale=sale,
            organization=self.org,
            return_reason="Item returned",
            refund_amount=Decimal("80.00"),
        )

        with self.assertRaises(PaymentValidationError):
            record_refund_for_return(sale_return)
