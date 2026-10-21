import uuid
from decimal import Decimal
from django.test import TestCase

from core.applications.invoice.models import Client, Invoice, InvoiceItem
from core.applications.notification.audit.action import AuditAction
from core.applications.notification.models import AuditLog
from core.applications.payment.models import InvoicePayment, Payment, Refund
from core.applications.sales.models import Sale, SaleItem, SaleReturn
from core.applications.sales.service import (
    create_invoice_for_sale,
    create_receipt_for_sale,
    create_sale,
    create_sale_return,
    mock_create_audit_event,
    mock_create_invoice,
    mock_create_payment,
    mock_create_receipt,
    record_payment_for_sale,
    record_refund_for_return,
    record_sale_audit_event,
    record_sale_return_audit_event,
)
from core.applications.customers.models import Customer, CustomerAddress
from core.applications.users.models import Membership
from core.applications.users.services import seed_default_roles
from core.applications.inventory.services import create_opening_stock
from core.applications.inventory.tests.factories import (
    OrganizationFactory,
    ProductFactory,
    StockLocationFactory,
    UserFactory,
)
from core.helper.enums import (
    InvoiceStatusChoices,
    PaymentStatus,
    RestockActionChoices,
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

    def test_backwards_compatibility_mock_aliases(self):
        """Verify mock function aliases point directly to real implementations."""
        self.assertIs(mock_create_invoice, create_invoice_for_sale)
        self.assertIs(mock_create_payment, record_payment_for_sale)
        self.assertIs(mock_create_receipt, create_receipt_for_sale)
        self.assertIs(mock_create_audit_event, record_sale_audit_event)

    def test_create_sale_creates_real_invoice_and_items(self):
        """Creating a sale automatically provisions a Client, Invoice, and InvoiceItems."""
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
        invoice = Invoice.objects.filter(extra_data__sale_id=str(sale.id)).first()
        self.assertIsNotNone(invoice)
        self.assertEqual(str(invoice.organization_id).replace("-", ""), str(self.org.id).replace("-", ""))
        self.assertEqual(str(invoice.warehouse_id).replace("-", ""), str(self.location.warehouse_id).replace("-", ""))
        self.assertEqual(invoice.sales_rep, self.user)
        self.assertEqual(invoice.total, Decimal("200.00"))
        self.assertEqual(invoice.status, InvoiceStatusChoices.CONFIRMED)

        # 3. Client provisioned and linked
        client = invoice.client
        self.assertEqual(str(client.organization_id).replace("-", ""), str(self.org.id).replace("-", ""))
        self.assertEqual(client.email, self.customer.email)
        self.assertEqual(client.name, self.customer.name)

        # 4. Invoice lines created
        lines = invoice.items.all()
        self.assertEqual(lines.count(), 1)
        self.assertEqual(str(lines.first().product_id).replace("-", ""), str(self.product.id).replace("-", ""))
        self.assertEqual(lines.first().quantity, 4)
        self.assertEqual(lines.first().unit_price, Decimal("50.00"))

        # 5. Audit Log generated
        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditAction.INVOICE_CREATED,
                resource_id=str(invoice.id).replace("-", ""),
            ).exists()
        )
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
            "payment_status": SalePaymentStatusChoices.PAID,
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

        # 1. Invoice marked as PAID
        invoice = Invoice.objects.get(extra_data__sale_id=str(sale.id))
        self.assertEqual(invoice.status, InvoiceStatusChoices.PAID)

        # 2. Payment recorded
        payment = Payment.objects.filter(description__contains=sale.sale_id).first()
        self.assertIsNotNone(payment)
        self.assertEqual(payment.amount, Decimal("100.00"))
        self.assertEqual(payment.status, PaymentStatus.SUCCESS)

        # 3. Receipt (InvoicePayment) recorded
        receipt = InvoicePayment.objects.filter(invoice=invoice, payment=payment).first()
        self.assertIsNotNone(receipt)
        self.assertEqual(receipt.amount, Decimal("100.00"))
        self.assertEqual(receipt.balance_after, Decimal("0.00"))
        self.assertTrue(receipt.receipt_number.startswith("R-"))

    def test_create_sale_unpaid_does_not_create_payment_or_receipt(self):
        """Unpaid sale generates Invoice in confirmed state without Payment or Receipt."""
        sale_data = {
            "organization": self.org,
            "customer": self.customer,
            "location": self.location,
            "sales_rep": self.user,
            "payment_status": SalePaymentStatusChoices.UNPAID,
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
        self.assertTrue(Invoice.objects.filter(extra_data__sale_id=str(sale.id)).exists())

        # No Payment or Receipt
        self.assertFalse(Payment.objects.filter(description__contains=sale.sale_id).exists())
        self.assertFalse(InvoicePayment.objects.filter(invoice__extra_data__sale_id=str(sale.id)).exists())

    def test_create_sale_return_restocks_and_creates_refund_and_audit(self):
        """Sale return against a paid sale creates Refund, restocks stock, and dispatches audit log."""
        # 1. Create a paid sale first
        sale_data = {
            "organization": self.org,
            "customer": self.customer,
            "location": self.location,
            "sales_rep": self.user,
            "payment_status": SalePaymentStatusChoices.PAID,
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
        self.assertIn(sale.sale_id, refund.payment.description)
