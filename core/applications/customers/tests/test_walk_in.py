"""The built-in Walk-in Customer: creation, protection, and its use in carts, sales and invoices."""

from __future__ import annotations

import uuid
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.test import TestCase
from rest_framework.test import APIClient

from core.applications.customers.models import Customer
from core.applications.customers.walk_in import WALK_IN_NAME
from core.applications.customers.walk_in import ensure_walk_in_customers
from core.applications.customers.walk_in import get_walk_in_customer
from core.applications.inventory.services import create_opening_stock
from core.applications.inventory.tests.factories import OrganizationFactory
from core.applications.inventory.tests.factories import ProductFactory
from core.applications.inventory.tests.factories import StockLocationFactory
from core.applications.inventory.tests.factories import UserFactory
from core.applications.sales.models import Sale
from core.applications.users.models import Membership
from core.applications.users.services import seed_default_roles


class WalkInCustomerTests(TestCase):
    def setUp(self):
        self.org = OrganizationFactory()
        plan = self.org.subscription.plan
        plan.max_locations = None
        plan.max_transactions_per_month = None
        plan.save(update_fields=["max_locations", "max_transactions_per_month"])
        roles = seed_default_roles(self.org)
        self.owner = UserFactory()
        Membership.objects.create(user=self.owner, organization=self.org, role=roles["owner"])
        self.location = StockLocationFactory(warehouse__organization=self.org)
        self.warehouse = self.location.warehouse
        self.product = ProductFactory(organization=self.org, customer_sale_price=Decimal("100.00"))
        create_opening_stock(self.product, None, None, self.warehouse, 10, self.owner)

        self.client = APIClient()
        self.client.force_authenticate(self.owner)
        self.org_url = f"/api/organizations/{self.org.pk}"

    # -------------------------------------------------------------- creation
    def test_one_per_organization_created_on_demand_and_by_backfill(self):
        walk_in = get_walk_in_customer(self.org)
        self.assertEqual(walk_in.name, WALK_IN_NAME)
        self.assertTrue(walk_in.is_walk_in)
        self.assertEqual(walk_in.credit_limit, Decimal("0.00"))
        self.assertEqual(uuid.UUID(str(get_walk_in_customer(self.org).pk)), uuid.UUID(str(walk_in.pk)))

        with self.assertRaises(IntegrityError), transaction.atomic():
            Customer.objects.create(organization=self.org, name="Second", is_walk_in=True)

        other = OrganizationFactory()
        self.assertGreaterEqual(ensure_walk_in_customers(), 1)
        self.assertTrue(Customer.objects.filter(organization=other, is_walk_in=True).exists())
        self.assertEqual(ensure_walk_in_customers(organization_ids=[other.pk]), 0)  # idempotent

    # ------------------------------------------------------------ protection
    def test_can_be_renamed_but_not_deactivated_deleted_or_given_credit(self):
        walk_in = get_walk_in_customer(self.org)
        url = f"{self.org_url}/customers/{walk_in.pk}/"

        response = self.client.patch(url, {"name": "Cash Customer"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json()["is_walk_in"])

        for body in ({"status": "inactive"}, {"credit_limit": "5000.00"}, {"payment_terms_days": 30}):
            response = self.client.patch(url, body, format="json")
            self.assertEqual(response.status_code, 409, body)
            self.assertEqual(response.json()["code"], "WALK_IN_CUSTOMER_PROTECTED")

        response = self.client.post(f"{url}adjust_credit/", {"credit_limit": "1000.00", "reason": "x"}, format="json")
        self.assertEqual(response.json()["code"], "WALK_IN_CUSTOMER_PROTECTED")
        self.assertEqual(self.client.delete(url).json()["code"], "WALK_IN_CUSTOMER_PROTECTED")
        self.assertTrue(Customer.objects.filter(pk=walk_in.pk, status="active").exists())

    # ------------------------------------------------------------- selling
    def test_new_cart_uses_walk_in_and_must_be_paid_in_full(self):
        carts = f"{self.org_url}/carts/"
        cart = self.client.post(carts, {"location": str(self.warehouse.pk)}, format="json").json()
        self.assertTrue(cart["customer"]["is_walk_in"])
        self.assertTrue(cart["checkout_ready"] is False)  # empty

        self.client.post(f"{carts}{cart['id']}/items/", {"product_id": str(self.product.pk), "quantity": 2},
                         format="json")
        checkout = f"{carts}{cart['id']}/checkout/"
        refused = self.client.post(checkout, {}, format="json")
        self.assertEqual(refused.status_code, 400)
        self.assertEqual(refused.json()["code"], "WALK_IN_MUST_PAY_IN_FULL")
        partial = self.client.post(checkout, {"payment": {"method": "cash", "amount": "50.00"}}, format="json")
        self.assertEqual(partial.json()["code"], "WALK_IN_MUST_PAY_IN_FULL")
        self.assertEqual(Sale.objects.count(), 0)

        paid = self.client.post(checkout, {"payment": {"method": "cash"}}, format="json")  # amount = full total
        self.assertEqual(paid.status_code, 201, paid.content)
        self.assertEqual(paid.json()["payment_status"], "paid")

    def test_clearing_the_customer_goes_back_to_walk_in(self):
        registered = Customer.objects.create(organization=self.org, name="Adaeze Stores")
        carts = f"{self.org_url}/carts/"
        cart_id = self.client.post(carts, {"location": str(self.warehouse.pk)}, format="json").json()["id"]
        body = self.client.patch(f"{carts}{cart_id}/", {"customer_id": str(registered.pk)}, format="json").json()
        self.assertFalse(body["customer"]["is_walk_in"])
        body = self.client.patch(f"{carts}{cart_id}/", {"customer_id": None}, format="json").json()
        self.assertTrue(body["customer"]["is_walk_in"])

    def test_direct_sale_without_customer_uses_walk_in(self):
        sales = f"{self.org_url}/sales/"
        item = {"product": str(self.product.pk), "quantity": 1}
        refused = self.client.post(sales, {"warehouse": str(self.warehouse.pk), "items": [item]}, format="json")
        self.assertEqual(refused.status_code, 400)
        self.assertEqual(refused.json()["code"], "WALK_IN_MUST_PAY_IN_FULL")

        response = self.client.post(
            sales,
            {"warehouse": str(self.warehouse.pk), "items": [item], "payment": {"method": "cash"}},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        sale = Sale.objects.get(pk=response.json()["id"])
        self.assertTrue(sale.customer.is_walk_in)

    def test_cannot_be_invoiced(self):
        walk_in = get_walk_in_customer(self.org)
        response = self.client.post(
            f"/api/organizations/{uuid.UUID(str(self.org.pk))}/invoices/",
            {
                "customer_id": str(walk_in.pk),
                "warehouse_id": str(self.warehouse.pk),
                "lines": [{"product_id": str(self.product.pk), "quantity": 1}],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(response.json()["code"], "WALK_IN_NOT_INVOICEABLE")
