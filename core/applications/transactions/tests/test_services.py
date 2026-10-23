from decimal import Decimal
import uuid
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.test import TestCase

from core.applications.customers.models import Customer
from core.applications.inventory.tests.factories import (
    OrganizationFactory,
    ProductFactory,
    StockLocationFactory,
    UserFactory,
)
from core.applications.payment.models import Payment
from core.applications.sales.models import Sale, SaleItem, SaleReturn, SaleReturnItem
from core.applications.transactions.models import FinancialLedgerEntry, Transaction
from core.applications.transactions.services import (
    FinancialEntryData,
    create_financial_entries,
    create_transaction,
    record_payment_financial_entries,
    record_sale_cancellation_financial_entries,
    record_sale_financial_entries,
    record_sale_return_financial_entries,
    TransactionData,
)
from core.helper.enums import (
    AccountNameChoices,
    PaymentMethod,
    PaymentStatus,
    RestockActionChoices,
    TransactionStatusChoices,
    TransactionTypeChoices,
)


class TransactionServicesTest(TestCase):
    def setUp(self):
        self.org = OrganizationFactory()
        self.user = UserFactory()
        self.location = StockLocationFactory(warehouse__organization=self.org)
        self.customer = Customer.objects.create(
            organization=self.org,
            name="Test Customer",
            email="cust@test.com",
        )
        self.transaction = Transaction.objects.create(
            transaction_id=f"TRX-TEST-{uuid.uuid4().hex[:6]}",
            organization=self.org,
            transaction_type=TransactionTypeChoices.SALE,
            status=TransactionStatusChoices.COMPLETED,
        )

    def test_create_financial_entries_balanced(self):
        entries = [
            FinancialEntryData(
                account_name=AccountNameChoices.ACCOUNTS_RECEIVABLE,
                debit=Decimal("100.00"),
            ),
            FinancialEntryData(
                account_name=AccountNameChoices.SALES_REVENUE,
                credit=Decimal("100.00"),
            ),
        ]
        created = create_financial_entries(self.transaction, entries)
        self.assertEqual(len(created), 2)
        self.assertEqual(FinancialLedgerEntry.objects.filter(transaction=self.transaction).count(), 2)

    def test_create_financial_entries_unbalanced_raises_error(self):
        entries = [
            FinancialEntryData(
                account_name=AccountNameChoices.ACCOUNTS_RECEIVABLE,
                debit=Decimal("100.00"),
            ),
            FinancialEntryData(
                account_name=AccountNameChoices.SALES_REVENUE,
                credit=Decimal("80.00"),
            ),
        ]
        with self.assertRaises(ValidationError) as ctx:
            create_financial_entries(self.transaction, entries)
        self.assertIn("Double-entry bookkeeping unbalanced", str(ctx.exception))

    def test_create_financial_entries_negative_amount_raises_error(self):
        entries = [
            FinancialEntryData(
                account_name=AccountNameChoices.ACCOUNTS_RECEIVABLE,
                debit=Decimal("-10.00"),
            ),
        ]
        with self.assertRaises(ValidationError):
            create_financial_entries(self.transaction, entries)

    def test_record_sale_financial_entries_balances(self):
        product = ProductFactory(
            organization=self.org,
            purchase_cost=Decimal("30.00"),
            customer_sale_price=Decimal("50.00"),
        )
        sale = Sale.objects.create(
            organization=self.org,
            customer=self.customer,
            warehouse=self.location.warehouse,
            location=self.location,
            sales_rep=self.user,
            sale_id=f"SALE-{uuid.uuid4().hex[:6]}",
            subtotal=Decimal("100.00"),
            discount_amount=Decimal("10.00"),
            tax_amount=Decimal("5.00"),
            total=Decimal("95.00"),
        )
        SaleItem.objects.create(
            sale=sale,
            product=product,
            quantity=2,
            unit_multiplier=Decimal("1.000"),
            unit_price=Decimal("50.00"),
        )

        entries = record_sale_financial_entries(sale, self.transaction)
        self.assertTrue(len(entries) >= 4)

        total_debits = sum(e.debit for e in entries)
        total_credits = sum(e.credit for e in entries)
        self.assertEqual(total_debits, total_credits)

        # Check accounts
        accounts = {e.account_name: (e.debit, e.credit) for e in entries}
        self.assertEqual(accounts[AccountNameChoices.ACCOUNTS_RECEIVABLE][0], Decimal("95.00"))
        self.assertEqual(accounts[AccountNameChoices.DISCOUNTS][0], Decimal("10.00"))
        self.assertEqual(accounts[AccountNameChoices.SALES_REVENUE][1], Decimal("100.00"))
        self.assertEqual(accounts[AccountNameChoices.TAX_PAYABLE][1], Decimal("5.00"))
        self.assertEqual(accounts[AccountNameChoices.COGS][0], Decimal("60.00"))
        self.assertEqual(accounts[AccountNameChoices.INVENTORY_ASSET][1], Decimal("60.00"))

    def test_record_payment_financial_entries_balances(self):
        payment = Payment.objects.create(
            user=self.user,
            amount=Decimal("95.00"),
            currency="USD",
            method=PaymentMethod.CASH,
            status=PaymentStatus.SUCCESS,
            reference="PAY-TEST-001",
        )
        entries = record_payment_financial_entries(payment, self.transaction)
        self.assertEqual(len(entries), 2)

        total_debits = sum(e.debit for e in entries)
        total_credits = sum(e.credit for e in entries)
        self.assertEqual(total_debits, total_credits)
        self.assertEqual(total_debits, Decimal("95.00"))

    def test_record_sale_cancellation_financial_entries_balances(self):
        product = ProductFactory(
            organization=self.org,
            purchase_cost=Decimal("20.00"),
            customer_sale_price=Decimal("50.00"),
        )
        sale = Sale.objects.create(
            organization=self.org,
            customer=self.customer,
            warehouse=self.location.warehouse,
            location=self.location,
            sales_rep=self.user,
            sale_id=f"SALE-CNL-{uuid.uuid4().hex[:6]}",
            subtotal=Decimal("50.00"),
            discount_amount=Decimal("5.00"),
            tax_amount=Decimal("2.50"),
            total=Decimal("47.50"),
        )
        SaleItem.objects.create(
            sale=sale,
            product=product,
            quantity=1,
            unit_multiplier=Decimal("1.000"),
            unit_price=Decimal("50.00"),
        )

        entries = record_sale_cancellation_financial_entries(sale, self.transaction)
        total_debits = sum(e.debit for e in entries)
        total_credits = sum(e.credit for e in entries)
        self.assertEqual(total_debits, total_credits)
