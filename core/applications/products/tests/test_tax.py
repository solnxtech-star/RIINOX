"""
Tax (PRD §13, §21, §39) and the Phase 2 approval rules, end to end.

Runs with the real DefaultInvoicePorts and DEBUG off, i.e. exactly as
production: creating a sale used to fail there because the ports were
development stand-ins.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from django.test import TestCase
from django.test import override_settings
from rest_framework.test import APIClient

from core.applications.customers.models import Customer
from core.applications.inventory.tests.factories import OrganizationFactory
from core.applications.inventory.tests.factories import ProductFactory
from core.applications.invoice.default_ports import DefaultInvoicePorts
from core.applications.invoice.exceptions import CreditLimitExceededError
from core.applications.invoice.models import Invoice
from core.applications.invoice.services import ports as invoice_ports
from core.applications.products import tax
from core.applications.sales.service import create_sale
from core.applications.sales.services.exceptions import SaleValidationError
from core.applications.users.models import Membership
from core.applications.users.services import seed_default_roles
from core.applications.users.tests.factories import UserFactory
from core.applications.warehouse.models import Warehouse
from core.helper.enums import TaxConfigChoices

D = Decimal


def _product(**kwargs):
    return SimpleNamespace(tax_config=kwargs.get("config", TaxConfigChoices.TAXABLE), tax_rate=kwargs.get("rate"))


class TaxCalculatorTests(TestCase):
    def test_rate_rules(self):
        policy = tax.TaxPolicy(default_rate=D("7.50"))

        self.assertEqual(tax.rate_for(_product(), policy), D("7.50"))  # org default
        self.assertEqual(tax.rate_for(_product(rate=D("5")), policy), D("5.00"))  # product override
        self.assertEqual(tax.rate_for(_product(rate=D("0")), policy), D("0.00"))  # explicit zero wins
        self.assertEqual(tax.rate_for(_product(config=TaxConfigChoices.EXEMPT), policy), D("0"))
        self.assertEqual(tax.rate_for(_product(config=TaxConfigChoices.NON_TAXABLE), policy), D("0"))

    def test_exclusive_adds_tax_on_top(self):
        result = tax.calculate([(_product(), D("1000.00"))], tax.TaxPolicy(default_rate=D("7.5")))

        self.assertEqual(result.tax, D("75.00"))
        self.assertEqual(result.total, D("1075.00"))

    def test_inclusive_extracts_tax_and_keeps_total(self):
        result = tax.calculate(
            [(_product(), D("1075.00"))], tax.TaxPolicy(default_rate=D("7.5"), prices_include_tax=True)
        )

        self.assertEqual(result.tax, D("75.00"))  # 1075 x 7.5 / 107.5
        self.assertEqual(result.total, D("1075.00"))

    def test_rounds_per_line_half_up_and_header_is_sum_of_lines(self):
        lines = [(_product(), D("0.10")), (_product(), D("0.10")), (_product(), D("0.10"))]

        result = tax.calculate(lines, tax.TaxPolicy(default_rate=D("5")))

        self.assertEqual([line.tax for line in result.lines], [D("0.01")] * 3)  # 0.005 -> 0.01
        self.assertEqual(result.tax, D("0.03"))

    def test_rejects_out_of_range_rate(self):
        with self.assertRaises(ValueError):
            tax.rate_for(_product(rate=D("150")), tax.TaxPolicy())

    def test_default_inclusive_by_business_type(self):
        self.assertTrue(tax.prices_include_tax_by_default("retail"))
        self.assertTrue(tax.prices_include_tax_by_default("pharmacy"))
        self.assertFalse(tax.prices_include_tax_by_default("wholesale_distribution"))
        self.assertFalse(tax.prices_include_tax_by_default(None))


@override_settings(DEBUG=False)
class SaleTaxAndApprovalTests(TestCase):
    """Production configuration: real ports, DEBUG off."""

    def setUp(self):
        self._previous_ports = invoice_ports._registry.get("ports")
        invoice_ports.register_invoice_ports(DefaultInvoicePorts())

        self.org = OrganizationFactory()
        self.org.default_tax_rate = D("7.50")
        self.org.save(update_fields=["default_tax_rate"])
        plan = self.org.subscription.plan
        plan.max_transactions_per_month = None
        plan.save(update_fields=["max_transactions_per_month"])
        self.roles = seed_default_roles(self.org)
        self.warehouse = Warehouse.objects.create(organization=self.org, name="Main", code="MAIN", is_default=True)
        self.owner = UserFactory()
        Membership.objects.create(user=self.owner, organization=self.org, role=self.roles["owner"])
        self.rep = UserFactory()
        Membership.objects.create(user=self.rep, organization=self.org, role=self.roles["sales-representative"])
        self.customer = Customer.objects.create(organization=self.org, name="Alpha", email="a@alpha.test")
        self.product = ProductFactory(organization=self.org, track_inventory=False, customer_sale_price=D("1000.00"))

    def tearDown(self):
        if self._previous_ports is not None:
            invoice_ports.register_invoice_ports(self._previous_ports)

    def _sale(self, actor, *, paid=True, **extra):
        data = {
            "organization": self.org,
            "customer": self.customer,
            "warehouse": self.warehouse,
            "sales_rep": actor,
            "items": [{"product": self.product, "product_name": "x", "quantity": 2, "unit_price": D("1000.00")}],
            **extra,
        }
        if paid:
            data["payment"] = {"method": "cash"}  # pay in full
        return create_sale(data, actor=actor)

    def test_sale_works_in_production_with_tax_added_on_top(self):
        sale = self._sale(self.owner)

        self.assertEqual(sale.tax_amount, D("150.00"))
        self.assertEqual(sale.total, D("2150.00"))
        self.assertFalse(sale.prices_include_tax)
        invoice = Invoice.objects.get(sale=sale)
        self.assertEqual((invoice.tax_amount, invoice.total), (D("150.00"), D("2150.00")))

    def test_tax_inclusive_organization(self):
        self.org.prices_include_tax = True
        self.org.save(update_fields=["prices_include_tax"])

        sale = self._sale(self.owner)

        self.assertEqual(sale.total, D("2000.00"))  # price already contains the tax
        self.assertEqual(sale.tax_amount, D("139.53"))  # 2000 x 7.5 / 107.5
        self.assertTrue(sale.prices_include_tax)
        self.assertTrue(Invoice.objects.get(sale=sale).prices_include_tax)
        self.assertEqual(sale.items.get().total, D("2000.00"))
        # The books balance and revenue excludes the tax inside the price.
        from core.applications.transactions.models import FinancialLedgerEntry
        from core.helper.enums import AccountNameChoices

        entries = FinancialLedgerEntry.objects.filter(transaction__object_id=str(sale.pk))
        self.assertEqual(
            sum(e.debit for e in entries), sum(e.credit for e in entries)
        )
        revenue = entries.get(account_name=AccountNameChoices.SALES_REVENUE).credit
        self.assertEqual(revenue, D("1860.47"))  # 2000.00 - 139.53

    def test_exempt_product_is_not_taxed(self):
        self.product.tax_config = TaxConfigChoices.EXEMPT
        self.product.save(update_fields=["tax_config"])

        sale = self._sale(self.owner)

        self.assertEqual((sale.tax_amount, sale.total), (D("0.00"), D("2000.00")))

    def test_client_supplied_tax_is_refused_by_the_service(self):
        with self.assertRaises(SaleValidationError):
            self._sale(self.owner, tax_amount=D("1.00"))

    def test_client_supplied_tax_is_refused_by_the_api(self):
        client = APIClient()
        client.force_authenticate(self.owner)

        response = client.post(
            f"/api/organizations/{self.org.pk}/sales/",
            {
                "customer": str(self.customer.pk),
                "warehouse": str(self.warehouse.pk),
                "tax_amount": "50.00",
                "items": [{"product": str(self.product.pk), "quantity": 1, "tax_rate": "5"}],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400, response.content)
        errors = response.data.get("errors", response.data)
        self.assertIn("tax_amount", errors)
        self.assertIn("items[0].tax_rate", errors)

    def test_credit_override_needs_approve_requests(self):
        # Unpaid sale with credit_limit 0: an override is required.
        self.assertIsNotNone(self._sale(self.owner, paid=False))  # owner holds APPROVE_REQUESTS
        with self.assertRaises(CreditLimitExceededError):
            self._sale(self.rep, paid=False)  # sales rep doesn't
