"""
End-to-end tests for the Reports app:
- Hand-verifiable numbers across Overview, Sales, Customers, Suppliers, Refunds, Payments, Inventory
- JSON & CSV exports (including CSV formula-injection escaping)
- Multi-tenancy, LocationScope, and role-based permission enforcement
- Report model clean() and ReportQuerySet tenant isolation
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from core.applications.customers.models import Customer
from core.applications.inventory.models import Inventory
from core.applications.inventory.services import create_opening_stock
from core.applications.inventory.tests.factories import OrganizationFactory
from core.applications.inventory.tests.factories import ProductFactory
from core.applications.invoice.models import Invoice
from core.applications.payment.models import InvoicePayment
from core.applications.payment.models import Payment
from core.applications.products.models import Category
from core.applications.products.models import ProductBatch
from core.applications.purchase.models import Purchase
from core.applications.purchase.models import PurchaseItem
from core.applications.report.models import Report
from core.applications.sales.models import SaleReturn
from core.applications.sales.service import create_sale
from core.applications.users.models import Membership
from core.applications.users.models import MembershipLocation
from core.applications.users.services import seed_default_roles
from core.applications.users.tests.factories import UserFactory
from core.applications.vendors.models import Vendor
from core.applications.warehouse.models import Warehouse
from core.helper.enums import PaymentMethod
from core.helper.enums import PaymentStatus
from core.helper.enums import PurchasePaymentStatusChoices
from core.helper.enums import PurchaseStatusChoices
from core.helper.enums import ReportFormatChoices
from core.helper.enums import ReportTypeChoice
from core.helper.enums import TransactionStatusChoices


class ReportTests(TestCase):
    def setUp(self):
        self.org = OrganizationFactory()
        self.org.default_tax_rate = Decimal("0")
        self.org.prices_include_tax = False
        self.org.save(update_fields=["default_tax_rate", "prices_include_tax"])
        plan = self.org.subscription.plan
        plan.max_locations = None
        plan.max_transactions_per_month = None
        plan.save(update_fields=["max_locations", "max_transactions_per_month"])
        roles = seed_default_roles(self.org)

        self.lekki = Warehouse.objects.create(
            organization=self.org, name="Lekki", code="LEK", is_default=True
        )
        self.ikeja = Warehouse.objects.create(
            organization=self.org, name="Ikeja", code="IKJ"
        )

        self.owner = UserFactory(name="Owner User")
        Membership.objects.create(user=self.owner, organization=self.org, role=roles["owner"])

        self.sales_manager = UserFactory(name="Sales Manager")
        Membership.objects.create(
            user=self.sales_manager, organization=self.org, role=roles["sales-manager"]
        )

        self.inventory_manager = UserFactory(name="Inventory Manager")
        Membership.objects.create(
            user=self.inventory_manager, organization=self.org, role=roles["inventory-manager"]
        )

        self.sales_rep = UserFactory(name="Sales Rep")
        rep_membership = Membership.objects.create(
            user=self.sales_rep,
            organization=self.org,
            role=roles["sales-representative"],
            all_locations=False,
        )
        MembershipLocation.objects.create(membership=rep_membership, location=self.lekki)

        self.branch_manager = UserFactory(name="Branch Manager")
        branch_membership = Membership.objects.create(
            user=self.branch_manager,
            organization=self.org,
            role=roles["manager"],
            all_locations=False,
        )
        MembershipLocation.objects.create(membership=branch_membership, location=self.lekki)

        self.other_org = OrganizationFactory()
        other_roles = seed_default_roles(self.other_org)
        self.outsider = UserFactory(name="Outsider")
        Membership.objects.create(
            user=self.outsider, organization=self.other_org, role=other_roles["owner"]
        )

        # Seed categories, products, customers, vendors, sales, payments, refunds, purchases
        self.grains = Category.objects.create(organization=self.org, name="Grains and flour")
        self.dairy = Category.objects.create(organization=self.org, name="Dairy")

        self.rice = ProductFactory(
            organization=self.org,
            category=self.grains,
            name="Premium Rice",
            purchase_cost=Decimal("150.00"),
            customer_sale_price=Decimal("250.00"),
            minimum_stock_level=20,
            created_by=self.owner,
        )
        self.milk = ProductFactory(
            organization=self.org,
            category=self.dairy,
            name="=Peak Milk",  # Tests CSV formula-injection escaping too
            purchase_cost=Decimal("100.00"),
            customer_sale_price=Decimal("200.00"),
            minimum_stock_level=2,
            created_by=self.owner,
        )
        create_opening_stock(self.rice, None, None, self.lekki, 10, self.owner)
        create_opening_stock(self.milk, None, None, self.lekki, 5, self.owner)

        # Expiring batch for milk
        self.expiring_batch = ProductBatch.objects.create(
            product=self.milk,
            batch_number="BATCH-001",
            expiry_date=timezone.localdate() + timedelta(days=10),
            cost_price=Decimal("100.00"),
            selling_price=Decimal("200.00"),
        )
        Inventory.objects.create(
            product=self.milk,
            batch=self.expiring_batch,
            warehouse=self.lekki,
            quantity=4,
        )

        # Returning customer (created 60 days ago) and new customer (created today)
        self.returning_customer = Customer.objects.create(
            organization=self.org, name="Coca Cola Ikeja", created_by=self.owner
        )
        Customer.objects.filter(pk=self.returning_customer.pk).update(
            created_at=timezone.now() - timedelta(days=60)
        )
        self.returning_customer.refresh_from_db()

        self.new_customer = Customer.objects.create(
            organization=self.org, name="Nasco Group", created_by=self.owner
        )

        # Sale 1: 2 x Rice = 500 (COGS = 300), paid via bank transfer
        self.sale_one = create_sale(
            {
                "organization": self.org,
                "customer": self.returning_customer,
                "warehouse": self.lekki,
                "sales_rep": self.owner,
                "items": [
                    {
                        "product": self.rice,
                        "product_name": self.rice.name,
                        "quantity": 2,
                        "unit_price": Decimal("250.00"),
                    }
                ],
            },
            actor=self.owner,
        )
        self.invoice_one = Invoice.objects.get(sale=self.sale_one)
        payment_one = Payment.objects.create(
            user=self.owner,
            amount=Decimal("500.00"),
            method=PaymentMethod.BANK_TRANSFER,
            status=PaymentStatus.SUCCESS,
            reference="PAY-REF-001",
        )
        InvoicePayment.objects.create(
            invoice=self.invoice_one,
            payment=payment_one,
            sales_rep=self.owner,
            amount=Decimal("500.00"),
            balance_after=Decimal("0.00"),
        )
        self.invoice_one.amount_paid = Decimal("500.00")
        self.invoice_one.status = "paid"
        self.invoice_one.save(update_fields=["amount_paid", "status"])

        # Sale 2: 1 x Milk = 200 (COGS = 100), unpaid (outstanding = 200)
        self.sale_two = create_sale(
            {
                "organization": self.org,
                "customer": self.new_customer,
                "warehouse": self.lekki,
                "sales_rep": self.owner,
                "items": [
                    {
                        "product": self.milk,
                        "product_name": self.milk.name,
                        "quantity": 1,
                        "unit_price": Decimal("200.00"),
                    }
                ],
            },
            actor=self.owner,
        )

        # Refund on Sale 1
        self.sale_return = SaleReturn.objects.create(
            original_sale=self.sale_one,
            organization=self.org,
            return_reason="Damaged goods",
            refund_amount=Decimal("50.00"),
            refund_method=PaymentMethod.CASH,
            status=TransactionStatusChoices.COMPLETED,
        )

        # Supplier & Purchase
        self.vendor = Vendor.objects.create(
            organization=self.org,
            company_name="Dangote Refinery",
            contact_person="Aliko",
            phone="08012345678",
            email="vendor@example.com",
        )
        self.purchase = Purchase.objects.create(
            organization=self.org,
            vendor=self.vendor,
            warehouse=self.lekki,
            ordered_by=self.owner,
            order_date=timezone.localdate(),
            status=PurchaseStatusChoices.ORDERED,
            payment_status=PurchasePaymentStatusChoices.UNPAID,
        )
        PurchaseItem.objects.create(
            purchase=self.purchase,
            product=self.rice,
            quantity_ordered=10,
            purchase_cost=Decimal("150.00"),
        )

        self.client = APIClient()

    def _url(self, suffix: str = "") -> str:
        base = f"/api/organizations/{self.org.pk}/reports/"
        return f"{base}{suffix}"

    def test_overview_report_figures_and_profit_redaction(self):
        self.client.force_authenticate(self.owner)
        response = self.client.get(self._url(), {"period": "this_month", "granularity": "monthly"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data
        self.assertIn("meta", data)
        self.assertTrue(data["meta"]["has_profit_permission"])
        # Sales = 500 + 200 = 700; COGS = 300 + 100 = 400; Gross profit = 300.00
        self.assertEqual(data["gross_profit_over_time"]["total"], "300.00")
        self.assertEqual(data["sales_by_category"]["total"], "700.00")
        self.assertEqual(data["payment_status_and_methods"]["total"], "700.00")
        self.assertEqual(len(data["browse_reports"]), 6)

        # Sales manager lacks VIEW_FINANCIAL_REPORTS / VIEW_DASHBOARD_PROFIT -> gross profit is None
        self.client.force_authenticate(self.sales_manager)
        mgr_response = self.client.get(self._url())
        self.assertEqual(mgr_response.status_code, status.HTTP_200_OK)
        self.assertIsNone(mgr_response.data["gross_profit_over_time"])
        self.assertFalse(mgr_response.data["meta"]["has_profit_permission"])

    def test_sales_report(self):
        self.client.force_authenticate(self.sales_manager)
        response = self.client.get(self._url("sales/"), {"period": "this_month", "granularity": "daily"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["sales_over_time"]["total"], "700.00")
        self.assertEqual(response.data["sales_over_time"]["order_count"], 2)
        self.assertEqual(response.data["top_products_by_sales"]["items"][0]["name"], "Premium Rice")
        self.assertEqual(response.data["top_customers_by_sales"]["items"][0]["name"], "Coca Cola Ikeja")

    def test_customer_report(self):
        self.client.force_authenticate(self.sales_manager)
        response = self.client.get(self._url("customers/"), {"period": "this_month"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["new_customers_over_time"]["total"], 1)
        self.assertEqual(response.data["outstanding_customer_balances"]["total_outstanding"], "200.00")
        self.assertEqual(response.data["outstanding_customer_balances"]["customer_count"], 1)
        self.assertEqual(response.data["sales_by_customer_type"]["total"], "700.00")

    def test_supplier_report(self):
        self.client.force_authenticate(self.inventory_manager)
        response = self.client.get(self._url("suppliers/"), {"period": "this_month"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["purchases_over_time"]["total"], "1500.00")
        self.assertEqual(response.data["spending_by_supplier"]["supplier_count"], 1)
        self.assertEqual(response.data["outstanding_supplier_balances"]["total_outstanding"], "1500.00")

    def test_refund_report(self):
        self.client.force_authenticate(self.sales_manager)
        response = self.client.get(self._url("refunds/"), {"period": "this_month"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["refunds_over_time"]["total"], "50.00")
        self.assertEqual(response.data["refunds"]["total_count"], 1)
        self.assertEqual(response.data["refunds"]["items"][0]["amount"], "50.00")

    def test_payment_report(self):
        self.client.force_authenticate(self.owner)
        response = self.client.get(self._url("payments/"), {"period": "this_month"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["payments_received_over_time"]["total"], "500.00")
        self.assertEqual(response.data["payments"]["total_count"], 1)
        self.assertEqual(response.data["payments"]["items"][0]["method"], PaymentMethod.BANK_TRANSFER)

    def test_inventory_report(self):
        self.client.force_authenticate(self.inventory_manager)
        response = self.client.get(self._url("inventory/"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Rice: 8 units * 150 = 1200; Milk: 4 (opening left) * 100 + 4 (batch) * 100 = 800 -> 2000
        self.assertEqual(response.data["stock_value_by_category"]["total_stock_worth"], "2000.00")
        self.assertEqual(response.data["low_stock"]["items"][0]["name"], "Premium Rice")
        self.assertEqual(response.data["expiring_soon"]["items"][0]["name"], "=Peak Milk")

    def test_export_json_and_csv_formula_escaping(self):
        self.client.force_authenticate(self.owner)
        json_response = self.client.get(self._url("refunds/export/"), {"export_format": "json"})
        self.assertEqual(json_response.status_code, status.HTTP_200_OK)
        self.assertEqual(json_response.data["report_type"], "refunds")
        self.assertEqual(len(json_response.data["refunds"]["items"]), 1)

        csv_response = self.client.get(self._url("refunds/export/"), {"export_format": "csv"})
        self.assertEqual(csv_response.status_code, status.HTTP_200_OK)
        self.assertIn("text/csv", csv_response["Content-Type"])

        # Formula injection check on inventory low_stock/category or customer name
        self.milk.name = "=Peak Milk"
        Customer.objects.filter(pk=self.new_customer.pk).update(name="=Evil Corp")
        customer_csv = self.client.get(self._url("customers/export/"), {"export_format": "csv"})
        self.assertEqual(customer_csv.status_code, status.HTTP_200_OK)
        self.assertIn("'=Evil Corp", customer_csv.content.decode("utf-8"))

    def test_permissions_and_tenant_location_isolation(self):
        # Cross-tenant user gets 404
        self.client.force_authenticate(self.outsider)
        self.assertEqual(self.client.get(self._url()).status_code, status.HTTP_404_NOT_FOUND)

        # Sales rep lacks report permissions -> 403
        self.client.force_authenticate(self.sales_rep)
        self.assertEqual(self.client.get(self._url()).status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(self.client.get(self._url("sales/")).status_code, status.HTTP_403_FORBIDDEN)

        # Sales manager cannot access payments or inventory -> 403
        self.client.force_authenticate(self.sales_manager)
        self.assertEqual(self.client.get(self._url("payments/")).status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(self.client.get(self._url("inventory/")).status_code, status.HTTP_403_FORBIDDEN)

        # Inventory manager cannot access sales -> 403
        self.client.force_authenticate(self.inventory_manager)
        self.assertEqual(self.client.get(self._url("sales/")).status_code, status.HTTP_403_FORBIDDEN)

        # Branch-restricted manager cannot query unassigned Ikeja location -> 403
        self.client.force_authenticate(self.branch_manager)
        self.assertEqual(
            self.client.get(self._url("sales/"), {"location": str(self.ikeja.pk)}).status_code,
            status.HTTP_403_FORBIDDEN,
        )
        self.assertEqual(
            self.client.get(self._url("sales/"), {"location": str(self.lekki.pk)}).status_code,
            status.HTTP_200_OK,
        )

    def test_report_model_clean_and_queryset_isolation(self):
        report_a = Report.objects.create(
            organization=self.org,
            report_type=ReportTypeChoice.SALES_SUMMARY,
            title="Org A Sales",
            format=ReportFormatChoices.CSV,
            generated_by=self.owner,
        )
        report_a.clean()

        # Cross-org generated_by fails clean()
        invalid_report = Report(
            organization=self.org,
            report_type=ReportTypeChoice.SALES_SUMMARY,
            title="Invalid",
            format=ReportFormatChoices.CSV,
            generated_by=self.outsider,
        )
        with self.assertRaises(ValidationError):
            invalid_report.clean()

        report_a.refresh_from_db()
        self.assertEqual(
            list(Report.objects.for_user(self.outsider).values_list("pk", flat=True)),
            [],
        )
        self.assertEqual(
            [str(pk) for pk in Report.objects.for_organization(self.org.pk).values_list("pk", flat=True)],
            [str(report_a.pk)],
        )
