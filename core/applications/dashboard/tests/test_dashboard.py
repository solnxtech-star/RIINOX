"""
Dashboard figures end to end, with numbers you can check by hand:

  Lekki: opening stock 10 units at cost 150, then a sale of 2 x 250 = 500
         (cost of goods 2 x 150 = 300), an expense of 100 and a refund of 50.
  Net profit = 500 - 300 - 100 - 50 = 50. Ikeja has nothing.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest import mock

from django.test import SimpleTestCase
from django.test import TestCase
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from core.applications.customers.models import Customer
from core.applications.dashboard.period import resolve_period
from core.applications.expenses.services import ExpenseInput
from core.applications.expenses.services import record_expense
from core.applications.inventory.models import StockAdjustmentRequest
from core.applications.inventory.services import create_opening_stock
from core.applications.inventory.tests.factories import OrganizationFactory
from core.applications.inventory.tests.factories import ProductFactory
from core.applications.sales.models import SaleReturn
from core.applications.sales.service import cancel_sale
from core.applications.sales.service import create_sale
from core.applications.users.models import Membership
from core.applications.users.models import MembershipLocation
from core.applications.users.services import seed_default_roles
from core.applications.users.tests.factories import UserFactory
from core.applications.warehouse.models import Warehouse
from core.helper.enums import ApprovalStatusChoices
from core.helper.enums import TransactionStatusChoices


class DashboardTests(TestCase):
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

        self.lekki = Warehouse.objects.create(organization=self.org, name="Lekki", code="LEK", is_default=True)
        self.ikeja = Warehouse.objects.create(organization=self.org, name="Ikeja", code="IKJ")

        self.owner = UserFactory(name="Chidi Okafor")
        Membership.objects.create(user=self.owner, organization=self.org, role=roles["owner"])
        self.rep = UserFactory()
        rep_membership = Membership.objects.create(
            user=self.rep, organization=self.org, role=roles["sales-representative"], all_locations=False
        )
        MembershipLocation.objects.create(membership=rep_membership, location=self.lekki)

        self.customer = Customer.objects.create(organization=self.org, name="ABC Stores", email="abc@x.test")
        self.product = ProductFactory(
            organization=self.org,
            name="Pet Coke",
            purchase_cost=Decimal("150.00"),
            customer_sale_price=Decimal("250.00"),
            track_inventory=True,
            minimum_stock_level=10,
        )
        create_opening_stock(self.product, None, None, self.lekki, 10, self.owner)

        self.client = APIClient()
        self.base = f"/api/organizations/{self.org.pk}/dashboard"

    # helpers ------------------------------------------------------------ #
    def _sale(self, quantity=2):
        return create_sale(
            {
                "organization": self.org,
                "customer": self.customer,
                "warehouse": self.lekki,
                "sales_rep": self.owner,
                "items": [{"product": self.product, "product_name": "Pet Coke", "quantity": quantity,
                           "unit_price": Decimal("250.00")}],
            },
            actor=self.owner,
        )

    def _expense(self, amount="100.00", location=None):
        return record_expense(
            organization=self.org,
            actor=self.owner,
            data=ExpenseInput(location=location or self.lekki, category="fuel", title="Fuel - Gen",
                              amount=Decimal(amount), payment_method="cash"),
        )

    def _get(self, path="", user=None, **params):
        self.client.force_authenticate(user or self.owner)
        params.setdefault("period", "last_30_days")
        return self.client.get(f"{self.base}/{path}", params)

    def _scenario(self):
        sale = self._sale()
        self._expense()
        SaleReturn.objects.create(
            original_sale=sale, organization=self.org, return_reason="Damaged",
            refund_amount=Decimal("50.00"), status=TransactionStatusChoices.COMPLETED,
        )
        return sale

    # overview ----------------------------------------------------------- #
    def test_overview_figures(self):
        self._scenario()
        response = self._get("overview/")

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        cards = response.data["cards"]
        self.assertEqual(cards["sales"]["value"], "500.00")
        self.assertEqual(cards["sales"]["count"], 1)
        self.assertEqual(cards["refunds"]["value"], "50.00")
        self.assertEqual(cards["expenses"]["value"], "100.00")
        self.assertEqual(cards["net_profit"]["cost_of_goods_sold"], "300.00")
        self.assertEqual(cards["net_profit"]["value"], "50.00")
        # Nothing in the previous 30 days: no percentage, trend up.
        self.assertIsNone(cards["sales"]["change_percent"])
        self.assertEqual(cards["sales"]["trend"], "up")
        self.assertTrue(response.data["meta"]["has_profit_permission"])

    def test_cancelled_sale_reverses_sales_and_cost(self):
        sale = self._sale()
        cancel_sale(sale, actor=self.owner, reason="Mistake")

        cards = self._get("overview/").data["cards"]
        self.assertEqual(cards["sales"]["value"], "0.00")
        self.assertEqual(cards["sales"]["count"], 0)
        self.assertEqual(cards["net_profit"]["cost_of_goods_sold"], "0.00")

    def test_location_filter_and_scope(self):
        self._scenario()
        ikeja = self._get("overview/", location=str(self.ikeja.pk)).data["cards"]
        self.assertEqual(ikeja["sales"]["value"], "0.00")
        self.assertEqual(ikeja["expenses"]["value"], "0.00")

        # The rep (Lekki only, no profit permission) sees Lekki sales, but no cost/profit cards.
        rep = self._get("overview/", user=self.rep)
        self.assertEqual(rep.status_code, status.HTTP_200_OK, rep.data)
        self.assertEqual(rep.data["cards"]["sales"]["value"], "500.00")
        self.assertIsNone(rep.data["cards"]["expenses"])
        self.assertIsNone(rep.data["cards"]["net_profit"])
        self.assertFalse(rep.data["meta"]["has_profit_permission"])

        denied = self._get("overview/", user=self.rep, location=str(self.ikeja.pk))
        self.assertEqual(denied.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(denied.data["code"], "LOCATION_ACCESS_DENIED")

    # chart, top products, alerts --------------------------------------- #
    def test_sales_chart(self):
        self._sale()
        data = self._get("sales-chart/").data
        self.assertEqual(data["granularity"], "week")
        self.assertEqual(data["total"], "500.00")
        self.assertEqual(sum(Decimal(p["value"]) for p in data["points"]), Decimal("500.00"))
        self.assertEqual(data["points"][0]["label"], "W1")

        today = self._get("sales-chart/", period="today").data
        self.assertEqual(today["granularity"], "hour")
        self.assertEqual(len(today["points"]), 24)
        self.assertEqual(sum(Decimal(p["value"]) for p in today["points"]), Decimal("500.00"))

    def test_top_products(self):
        self._sale(quantity=2)
        data = self._get("top-products/").data
        self.assertEqual(data["items"][0]["product"]["name"], "Pet Coke")
        self.assertEqual(data["items"][0]["units_sold"], 2)
        self.assertEqual(data["items"][0]["revenue"], "500.00")
        self.assertEqual(data["max_units_sold"], 2)

    def test_alerts(self):
        self._sale()  # 10 - 2 = 8 left, minimum 10: low stock
        StockAdjustmentRequest.objects.create(
            product=self.product, warehouse=self.lekki, requested_quantity_change=-1,
            requested_by=self.rep, status=ApprovalStatusChoices.PENDING,
        )
        types = {item["type"]: item for item in self._get("alerts/").data["items"]}
        self.assertEqual(types["low_stock"]["count"], 1)
        self.assertEqual(types["pending_approvals"]["count"], 1)

        # Approvals are only shown to people who can approve.
        rep_types = {item["type"] for item in self._get("alerts/", user=self.rep).data["items"]}
        self.assertNotIn("pending_approvals", rep_types)

    # lists -------------------------------------------------------------- #
    def test_recent_transactions_and_activity(self):
        self._scenario()
        data = self._get("recent-transactions/").data
        self.assertEqual(data["total"], 3)
        self.assertEqual({row["type"] for row in data["items"]}, {"sale", "expense", "refund"})
        expense = next(row for row in data["items"] if row["type"] == "expense")
        self.assertEqual(expense["type_display"], "Operational Expenses")
        self.assertEqual(expense["branch"]["name"], "Lekki")

        activity = self._get("activity/").data
        self.assertTrue(activity["available"])
        messages = [item["message"] for item in activity["items"]]
        self.assertTrue(any("Chidi Okafor recorded expense Fuel - Gen" in m for m in messages), messages)

        self.assertFalse(self._get("activity/", user=self.rep).data["available"])
        rep_types = {row["type"] for row in self._get("recent-transactions/", user=self.rep).data["items"]}
        self.assertNotIn("expense", rep_types)

    def test_whole_dashboard_and_validation(self):
        self._scenario()
        data = self._get("").data
        for section in ("meta", "overview", "sales_chart", "alerts", "top_products", "recent_activity",
                        "recent_transactions"):
            self.assertIn(section, data)
        self.assertEqual(data["overview"]["cards"]["net_profit"]["value"], "50.00")

        self.assertEqual(self._get("overview/", period="forever").status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self._get("overview/", period="custom").status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self._get("recent-transactions/", limit="0").status_code, status.HTTP_400_BAD_REQUEST)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(f"{self.base}/").status_code, status.HTTP_401_UNAUTHORIZED)


class PeriodTests(SimpleTestCase):
    def _resolve(self, today, **params):
        with mock.patch("core.applications.dashboard.period.organization_today", return_value=today):
            return resolve_period(params, organization=None)

    def test_to_date_periods_compare_like_with_like(self):
        p = self._resolve(date(2026, 11, 17), period="this_month")
        self.assertEqual((p.start, p.end), (date(2026, 11, 1), date(2026, 11, 17)))
        self.assertEqual((p.previous_start, p.previous_end), (date(2026, 10, 1), date(2026, 10, 17)))

        # 31 March compares with 1-28 Feb (no 31 Feb).
        p = self._resolve(date(2026, 3, 31), period="this_month")
        self.assertEqual((p.previous_start, p.previous_end), (date(2026, 2, 1), date(2026, 2, 28)))

    def test_closed_and_rolling_periods(self):
        p = self._resolve(date(2026, 1, 10), period="last_month")
        self.assertEqual((p.start, p.end), (date(2025, 12, 1), date(2025, 12, 31)))
        self.assertEqual((p.previous_start, p.previous_end), (date(2025, 11, 1), date(2025, 11, 30)))

        p = self._resolve(date(2026, 11, 17), period="last_7_days")
        self.assertEqual((p.start, p.previous_end), (date(2026, 11, 11), date(2026, 11, 10)))

        p = self._resolve(date(2026, 11, 17), period="this_quarter")
        self.assertEqual((p.start, p.previous_start, p.previous_end),
                         (date(2026, 10, 1), date(2026, 7, 1), date(2026, 8, 17)))

    def test_custom_validation(self):
        today = date(2026, 11, 17)
        p = self._resolve(today, period="custom", start_date="2026-11-01", end_date="2026-11-10")
        self.assertEqual((p.previous_start, p.previous_end), (date(2026, 10, 22), date(2026, 10, 31)))
        for bad in (
            {"start_date": "2026-11-10", "end_date": "2026-11-01"},
            {"start_date": "2026-11-01", "end_date": "2026-11-30"},
            {"start_date": "2024-01-01", "end_date": "2026-11-01"},
            {"start_date": "nope", "end_date": "2026-11-01"},
        ):
            with self.assertRaises(ValidationError):
                self._resolve(today, period="custom", **bad)
