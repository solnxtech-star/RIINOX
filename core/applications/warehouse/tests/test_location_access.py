"""
Location access (PRD §35) end to end: what a member limited to one location can
see and do, compared with an owner who has every location.

Setup: one organization with two locations, Lekki and Ikeja. One product has 30
units at Lekki and 70 at Ikeja. `staff` (inventory staff) may only work in Lekki.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from django.test import TestCase
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APIClient

from core.applications.customers.models import Customer
from core.applications.inventory.models import Inventory
from core.applications.inventory.services import approve_stock_adjustment
from core.applications.inventory.tests.factories import InventoryFactory
from core.applications.inventory.tests.factories import OrganizationFactory
from core.applications.inventory.tests.factories import ProductFactory
from core.applications.inventory.tests.factories import StockAdjustmentRequestFactory
from core.applications.invoice.models import Invoice
from core.applications.invoice.services import invoices as invoice_services
from core.applications.sales.service import create_sale
from core.applications.users import defaults
from core.applications.users.models import Membership
from core.applications.users.models import MembershipLocation
from core.applications.users.services import seed_default_roles
from core.applications.users.tests.factories import UserFactory
from core.applications.warehouse import access
from core.applications.warehouse.access import LocationScope
from core.applications.warehouse.models import Warehouse
from core.helper.enums import ApprovalStatusChoices


def _ids(response) -> set[str]:
    data = response.data
    rows = data.get("results", data) if isinstance(data, dict) else data
    return {str(uuid.UUID(str(row["id"]))) for row in rows}


def _uid(value) -> str:
    return str(uuid.UUID(str(value)))


class LocationScopeUnitTests(TestCase):
    def test_role_slugs_match_users_defaults(self):
        # access.py duplicates these to avoid importing users at load time.
        self.assertEqual(
            access.ALL_LOCATION_ROLE_SLUGS,
            {defaults.OWNER_ROLE_SLUG, defaults.ADMINISTRATOR_ROLE_SLUG},
        )

    def test_scope_rules(self):
        lekki, ikeja = uuid.uuid4(), uuid.uuid4()
        limited = LocationScope(all_locations=False, location_ids=frozenset({lekki}))

        self.assertTrue(LocationScope.everything().allows(ikeja))
        self.assertTrue(LocationScope.everything().allows(None))  # record without a location
        self.assertTrue(limited.allows(lekki))
        self.assertTrue(limited.allows(str(lekki).replace("-", "")))  # any id format
        self.assertFalse(limited.allows(ikeja))
        self.assertFalse(limited.allows(None))
        self.assertFalse(LocationScope.nothing().allows(lekki))


class LocationAccessAPITests(TestCase):
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
        self.staff = UserFactory()
        self.staff_membership = Membership.objects.create(
            user=self.staff, organization=self.org, role=self.roles["inventory-staff"], all_locations=False
        )
        MembershipLocation.objects.create(membership=self.staff_membership, location=self.lekki)

        self.product = ProductFactory(organization=self.org)
        InventoryFactory(product=self.product, warehouse=self.lekki, quantity=30)
        InventoryFactory(product=self.product, warehouse=self.ikeja, quantity=70)

        self.client = APIClient()
        self.base = f"/api/organizations/{self.org.pk}"

    def _as(self, user):
        self.client.force_authenticate(user)
        return self.client

    # --- locations ------------------------------------------------------- #
    def test_location_list_only_contains_callers_locations(self):
        staff_ids = _ids(self._as(self.staff).get(f"{self.base}/warehouses/"))
        owner_ids = _ids(self._as(self.owner).get(f"{self.base}/warehouses/"))

        self.assertEqual(staff_ids, {_uid(self.lekki.pk)})
        self.assertEqual(owner_ids, {_uid(self.lekki.pk), _uid(self.ikeja.pk)})

    def test_location_outside_scope_is_404(self):
        response = self._as(self.staff).get(f"{self.base}/warehouses/{self.ikeja.pk}/")

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    # --- stock ----------------------------------------------------------- #
    def test_stock_balances_are_scoped_and_filterable(self):
        staff = self._as(self.staff)
        lekki_rows = staff.get(f"{self.base}/inventory/balances/")
        ikeja_rows = staff.get(f"{self.base}/inventory/balances/?location={self.ikeja.pk}")

        self.assertEqual(lekki_rows.status_code, status.HTTP_200_OK)
        self.assertEqual(len(_ids(lekki_rows)), 1)
        self.assertEqual(_ids(ikeja_rows), set())  # outside scope: empty, not an error

    def test_malformed_location_param_is_400(self):
        response = self._as(self.owner).get(f"{self.base}/inventory/balances/?location=not-a-uuid")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    # --- products -------------------------------------------------------- #
    def _stock(self, client, query=""):
        response = client.get(f"{self.base}/products/{query}")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        rows = response.data.get("results", response.data) if isinstance(response.data, dict) else response.data
        return {_uid(row["id"]): row["current_stock"] for row in rows}

    def test_product_stock_is_summed_over_callers_locations(self):
        pid = _uid(self.product.pk)

        self.assertEqual(self._stock(self._as(self.owner))[pid], 100)
        self.assertEqual(self._stock(self._as(self.staff))[pid], 30)
        self.assertEqual(self._stock(self._as(self.owner), f"?location={self.ikeja.pk}")[pid], 70)

    def test_catalogue_is_shared_but_in_stock_filters_per_location(self):
        empty_everywhere = ProductFactory(organization=self.org)
        staff = self._as(self.staff)

        all_products = self._stock(staff)
        in_stock = self._stock(staff, "?in_stock=true")

        self.assertIn(_uid(empty_everywhere.pk), all_products)  # catalogue is organization-wide
        self.assertNotIn(_uid(empty_everywhere.pk), in_stock)
        self.assertIn(_uid(self.product.pk), in_stock)

    def test_low_stock_filter(self):
        self.product.minimum_stock_level = 50
        self.product.save(update_fields=["minimum_stock_level"])

        owner_low = self._stock(self._as(self.owner), "?low_stock=true")  # 100 > 50
        lekki_low = self._stock(self._as(self.owner), f"?low_stock=true&location={self.lekki.pk}")  # 30 <= 50

        self.assertNotIn(_uid(self.product.pk), owner_low)
        self.assertIn(_uid(self.product.pk), lekki_low)

    def test_product_list_query_count_does_not_grow_with_products(self):
        for _ in range(5):
            InventoryFactory(product=ProductFactory(organization=self.org), warehouse=self.lekki, quantity=1)
        client = self._as(self.owner)
        client.get(f"{self.base}/products/")  # warm caches (membership, content types)

        with self.assertNumQueriesLessThan(25):
            client.get(f"{self.base}/products/")

    def assertNumQueriesLessThan(self, limit):  # noqa: N802
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        test = self

        class _Ctx(CaptureQueriesContext):
            def __exit__(self, *exc):
                super().__exit__(*exc)
                test.assertLess(len(self.captured_queries), limit, "product list issued too many queries")

        return _Ctx(connection)

    # --- writes ---------------------------------------------------------- #
    def test_adjustment_outside_scope_is_403(self):
        response = self._as(self.staff).post(
            f"{self.base}/inventory/adjustments/",
            {"product": str(self.product.pk), "warehouse": str(self.ikeja.pk), "requested_quantity_change": -1},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN, response.content)
        self.assertIn("LOCATION_ACCESS_DENIED", str(response.content))

    def test_transfer_needs_access_to_both_locations(self):
        response = self._as(self.staff).post(
            f"{self.base}/inventory/transfers/execute/",
            {
                "product_id": str(self.product.pk),
                "source_warehouse_id": str(self.lekki.pk),
                "destination_warehouse_id": str(self.ikeja.pk),
                "quantity": 1,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN, response.content)
        self.assertEqual(Inventory.objects.get(product=self.product, warehouse=self.lekki).quantity, 30)

    def test_limited_member_cannot_create_locations(self):
        response = self._as(self.staff).post(
            f"{self.base}/warehouses/", {"name": "Abuja", "code": "ABJ"}, format="json"
        )

        # inventory staff lacks MANAGE_LOCATIONS anyway; either way it must not be created
        self.assertIn(response.status_code, (status.HTTP_403_FORBIDDEN,))
        self.assertFalse(Warehouse.objects.filter(code="ABJ").exists())


class StockAdjustmentApprovalSecurityTests(TestCase):
    """Regression: approving used to look the request up by id only, across tenants."""

    def test_approval_is_tenant_scoped(self):
        own_org, other_org = OrganizationFactory(), OrganizationFactory()
        other_wh = Warehouse.objects.create(organization=other_org, name="Other", code="OTH")
        foreign = StockAdjustmentRequestFactory(
            product=ProductFactory(organization=other_org), warehouse=other_wh
        )

        with self.assertRaises(StockAdjustmentRequestFactory._meta.model.DoesNotExist):
            approve_stock_adjustment(foreign.pk, UserFactory(), organization=own_org)

        foreign.refresh_from_db()
        self.assertEqual(foreign.status, ApprovalStatusChoices.PENDING)


@override_settings(DEBUG=True)  # the default invoice ports' tax/approval stand-ins only run in DEBUG
class SalesAndInvoicesLocationTests(TestCase):
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
        self.rep = UserFactory()
        rep_membership = Membership.objects.create(
            user=self.rep, organization=self.org, role=self.roles["sales-representative"], all_locations=False
        )
        MembershipLocation.objects.create(membership=rep_membership, location=self.lekki)

        self.customer = Customer.objects.create(organization=self.org, name="Alpha", email="a@alpha.test")
        self.product = ProductFactory(
            organization=self.org, track_inventory=False, customer_sale_price=Decimal("10.00")
        )
        self.lekki_sale = self._sale(self.lekki)
        self.ikeja_sale = self._sale(self.ikeja)

        self.client = APIClient()
        self.base = f"/api/organizations/{self.org.pk}"

    def _sale(self, warehouse):
        return create_sale(
            {
                "organization": self.org,
                "customer": self.customer,
                "warehouse": warehouse,
                "sales_rep": self.owner,
                "items": [
                    {
                        "product": self.product,
                        "product_name": self.product.name,
                        "quantity": 1,
                        "unit_price": Decimal("10.00"),
                    }
                ],
            },
            actor=self.owner,
        )

    def test_sales_list_is_scoped(self):
        self.client.force_authenticate(self.rep)

        response = self.client.get(f"{self.base}/sales/")

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertEqual(_ids(response), {_uid(self.lekki_sale.pk)})

    def test_sale_at_other_location_is_404(self):
        self.client.force_authenticate(self.rep)

        response = self.client.get(f"{self.base}/sales/{self.ikeja_sale.pk}/")

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_sale_without_location_uses_default_location(self):
        sale = create_sale(
            {
                "organization": self.org,
                "customer": self.customer,
                "sales_rep": self.owner,
                "items": [
                    {"product": self.product, "product_name": "x", "quantity": 1, "unit_price": Decimal("10.00")}
                ],
            },
            actor=self.owner,
        )

        self.assertEqual(_uid(sale.warehouse_id), _uid(self.lekki.pk))  # Lekki is default, Ikeja sorts first

    def test_invoices_are_scoped_in_services(self):
        visible = invoice_services.invoice_queryset(self.org, actor=self.rep)
        ikeja_invoice = Invoice.objects.get(sale=self.ikeja_sale)

        self.assertEqual({_uid(i.warehouse_id) for i in visible}, {_uid(self.lekki.pk)})
        with self.assertRaises(Exception) as ctx:
            invoice_services.get_invoice(self.org, ikeja_invoice.pk, actor=self.rep)
        self.assertEqual(type(ctx.exception).__name__, "InvoiceNotFoundError")
