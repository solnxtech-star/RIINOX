"""Server-side carts: opening, items, pricing, stock, hold/resume, checkout, permissions, expiry."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from core.applications.customers.models import Customer
from core.applications.inventory.models import Inventory
from core.applications.inventory.services import create_opening_stock
from core.applications.inventory.tests.factories import OrganizationFactory
from core.applications.inventory.tests.factories import ProductFactory
from core.applications.inventory.tests.factories import StockLocationFactory
from core.applications.inventory.tests.factories import UserFactory
from core.applications.products.models import ProductUnitConversion
from core.applications.sales.models import Cart
from core.applications.sales.models import CartItem
from core.applications.sales.models import CartStatus
from core.applications.sales.models import Sale
from core.applications.sales.services.carts import MAX_HELD_CARTS
from core.applications.sales.services.carts import expire_idle_carts
from core.applications.users.models import Membership
from core.applications.users.models import MembershipLocation
from core.applications.users.services import seed_default_roles
from core.applications.warehouse.models import Warehouse
from core.helper.enums import ProductStatusChoices


class CartTestBase(TestCase):
    def setUp(self):
        self.org = OrganizationFactory()
        plan = self.org.subscription.plan
        plan.max_locations = None
        plan.max_transactions_per_month = None
        plan.save(update_fields=["max_locations", "max_transactions_per_month"])
        roles = seed_default_roles(self.org)

        self.owner = UserFactory()
        Membership.objects.create(user=self.owner, organization=self.org, role=roles["owner"])
        self.rep = UserFactory()  # CREATE_SALE, no APPLY_DISCOUNT, no EDIT_SELLING_PRICE
        Membership.objects.create(user=self.rep, organization=self.org, role=roles["sales-representative"])
        self.staff = UserFactory()  # no CREATE_SALE
        Membership.objects.create(user=self.staff, organization=self.org, role=roles["inventory-staff"])

        self.location = StockLocationFactory(warehouse__organization=self.org)
        self.warehouse = self.location.warehouse
        self.other_warehouse = Warehouse.objects.create(organization=self.org, name="Ikeja", code="IKJ")
        self.limited = UserFactory()
        membership = Membership.objects.create(
            user=self.limited, organization=self.org, role=roles["sales-representative"], all_locations=False
        )
        MembershipLocation.objects.create(membership=membership, location=self.warehouse)

        self.product = ProductFactory(organization=self.org, customer_sale_price=Decimal("100.00"))
        create_opening_stock(self.product, None, None, self.warehouse, 30, self.owner)
        ProductUnitConversion.objects.create(product=self.product, unit_name="carton", multiplier=Decimal("12"))
        self.customer = Customer.objects.create(organization=self.org, name="Adaeze Stores")

        self.base = f"/api/organizations/{self.org.pk}/carts/"
        self.client = APIClient()

    def _as(self, user):
        self.client.force_authenticate(user)
        return self.client

    def _open(self, user=None, **body):
        body.setdefault("location", str(self.warehouse.pk))
        return self._as(user or self.owner).post(self.base, body, format="json")

    def _cart(self, user=None):
        return self._open(user).json()["id"]

    def _add(self, cart_id, user=None, **body):
        body.setdefault("product_id", str(self.product.pk))
        return self._as(user or self.owner).post(f"{self.base}{cart_id}/items/", body, format="json")


class OpenAndItemsTests(CartTestBase):
    def test_open_is_idempotent_per_member_and_location(self):
        first = self._open()
        self.assertEqual(first.status_code, 201, first.content)
        again = self._open()
        self.assertEqual(again.status_code, 200)
        self.assertEqual(again.json()["id"], first.json()["id"])
        self.assertNotEqual(self._open(user=self.rep).json()["id"], first.json()["id"])
        self.assertEqual(Cart.objects.count(), 2)

    def test_add_merges_same_line_and_prices_on_server(self):
        cart_id = self._cart()
        self._add(cart_id, quantity=2)
        body = self._add(cart_id, quantity=3).json()
        self.assertEqual(len(body["lines"]), 1)
        line = body["lines"][0]
        self.assertEqual(line["quantity"], 5)
        self.assertEqual(line["unit_price"], "100.00")
        self.assertEqual(body["subtotal"], "500.00")
        self.assertEqual(line["available_quantity"], 30)
        self.assertTrue(line["in_stock"])
        self.assertEqual(body["version"], 3)

    def test_other_unit_is_priced_per_unit(self):
        cart_id = self._cart()
        line = self._add(cart_id, quantity=2, unit_name="carton").json()["lines"][0]
        self.assertEqual(line["unit_price"], "1200.00")  # 12 x 100, not the piece price
        self.assertEqual(line["subtotal"], "2400.00")
        self.assertEqual(line["available_quantity"], 2)  # 30 pieces = 2 whole cartons
        self.assertEqual(self._add(cart_id, unit_name="crate").status_code, 400)

    def test_update_and_remove_lines(self):
        cart_id = self._cart()
        item_id = self._add(cart_id, quantity=2).json()["lines"][0]["id"]
        url = f"{self.base}{cart_id}/items/{item_id}/"
        self.assertEqual(self.client.patch(url, {"quantity": 4}, format="json").json()["lines"][0]["quantity"], 4)
        self.assertEqual(self.client.patch(url, {"quantity": 0}, format="json").json()["lines"], [])
        item_id = self._add(cart_id).json()["lines"][0]["id"]
        self.assertEqual(self.client.delete(f"{self.base}{cart_id}/items/{item_id}/").json()["lines"], [])
        self.assertEqual(self.client.delete(f"{self.base}{cart_id}/items/{item_id}/").status_code, 404)

    def test_out_of_stock_is_flagged_not_blocked(self):
        cart_id = self._cart()
        self._add(cart_id, quantity=20)
        body = self._add(cart_id, quantity=1, unit_name="carton").json()  # 20 + 12 > 30
        self.assertFalse(all(line["in_stock"] for line in body["lines"]))
        self.assertIn("OUT_OF_STOCK", {issue["code"] for issue in body["issues"]})
        self.assertFalse(body["checkout_ready"])

    def test_inactive_product_and_unknown_fields(self):
        cart_id = self._cart()
        self.product.status = ProductStatusChoices.INACTIVE
        self.product.save(update_fields=["status"])
        self.assertEqual(self._add(cart_id).json()["code"], "PRODUCT_UNAVAILABLE")
        response = self._add(cart_id, price="1.00")
        self.assertEqual(response.status_code, 400)
        self.assertIn("price", response.json()["errors"])

    def test_discount_and_price_override_need_rights(self):
        cart_id = self._cart(user=self.rep)
        self.assertEqual(self._add(cart_id, user=self.rep, discount="5.00").json()["code"], "DISCOUNT_NOT_ALLOWED")
        self.assertEqual(
            self._add(cart_id, user=self.rep, unit_price="50.00").json()["code"], "PRICE_OVERRIDE_NOT_ALLOWED"
        )
        self.assertEqual(self._add(cart_id, user=self.rep, unit_price="100.00").status_code, 200)  # catalog price

        owner_cart = self._cart()
        body = self._add(owner_cart, unit_price="80.00", discount="10.00").json()
        self.assertTrue(body["lines"][0]["price_overridden"])
        self.assertEqual(body["total"], "70.00")

    def test_cart_discount_rules(self):
        cart_id = self._cart()
        self._add(cart_id, quantity=2)
        url = f"{self.base}{cart_id}/"
        body = self.client.patch(url, {"discount_amount": "50.00", "customer_id": str(self.customer.pk)},
                                 format="json").json()
        self.assertEqual(body["discount_amount"], "50.00")
        self.assertEqual(body["customer"]["name"], "Adaeze Stores")
        self.assertEqual(self.client.patch(url, {"discount_amount": "999.00"}, format="json").status_code, 400)
        self.assertEqual(Cart.objects.get(pk=cart_id).discount_amount, Decimal("50.00"))  # rolled back

    def test_only_own_carts_and_permissions(self):
        cart_id = self._cart()
        self.assertEqual(self._as(self.rep).get(f"{self.base}{cart_id}/").status_code, 404)
        self.assertEqual(self._add(cart_id, user=self.rep).status_code, 404)
        self.assertEqual(self._open(user=self.staff).status_code, 403)
        self.assertEqual(self._as(UserFactory()).get(self.base).status_code, 404)

    def test_location_scope(self):
        self.assertEqual(self._open(user=self.limited, location=str(self.other_warehouse.pk)).status_code, 403)
        self.assertEqual(
            self._as(self.limited).post(self.base, {}, format="json").json()["code"], "LOCATION_REQUIRED"
        )
        self.assertEqual(self._open(user=self.limited).status_code, 201)


class HoldResumeTests(CartTestBase):
    def test_hold_then_fresh_cart_then_resume_swaps(self):
        first = self._cart()
        self._add(first)
        self.assertEqual(self.client.post(f"{self.base}{first}/hold/", {"label": "Mrs Ade"}, format="json")
                         .json()["status"], "held")
        second = self._cart()
        self.assertNotEqual(first, second)
        self._add(second)

        held = self.client.get(self.base, {"status": "held"}).json()
        self.assertEqual([row["label"] for row in held["results"]], ["Mrs Ade"])
        self.assertEqual(held["results"][0]["item_count"], 1)

        resumed = self.client.post(f"{self.base}{first}/resume/").json()
        self.assertEqual(resumed["status"], "active")
        self.assertEqual(Cart.objects.get(pk=second).status, CartStatus.HELD)  # not lost

    def test_resume_deletes_an_empty_active_cart(self):
        first = self._cart()
        self._add(first)
        self.client.post(f"{self.base}{first}/hold/")
        empty = self._cart()
        self.client.post(f"{self.base}{first}/resume/")
        self.assertFalse(Cart.objects.filter(pk=empty).exists())

    def test_hold_rules(self):
        empty = self._cart()
        self.assertEqual(self.client.post(f"{self.base}{empty}/hold/").json()["code"], "CART_EMPTY")
        for _ in range(MAX_HELD_CARTS):
            cart_id = self._cart()
            self._add(cart_id)
            self.client.post(f"{self.base}{cart_id}/hold/")
        cart_id = self._cart()
        self._add(cart_id)
        self.assertEqual(self.client.post(f"{self.base}{cart_id}/hold/").json()["code"], "HELD_CART_LIMIT")

    def test_discard_and_clear(self):
        cart_id = self._cart()
        self._add(cart_id)
        self.assertEqual(self.client.post(f"{self.base}{cart_id}/clear/").json()["lines"], [])
        self.assertEqual(self.client.delete(f"{self.base}{cart_id}/").status_code, 204)
        self.assertEqual(self._add(cart_id).json()["code"], "CART_CLOSED")
        self.assertNotEqual(self._cart(), cart_id)


class CheckoutTests(CartTestBase):
    def _ready_cart(self, quantity=3):
        cart_id = self._cart()
        self._add(cart_id, quantity=quantity)
        self.client.patch(f"{self.base}{cart_id}/", {"customer_id": str(self.customer.pk)}, format="json")
        return cart_id

    def test_checkout_creates_sale_deducts_stock_and_is_idempotent(self):
        cart_id = self._ready_cart(quantity=3)
        url = f"{self.base}{cart_id}/checkout/"
        response = self.client.post(url, {"payment": {"method": "cash", "amount": "500.00"}}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        sale = Sale.objects.get(pk=response.json()["id"])
        self.assertEqual(sale.total, Decimal("300.00"))
        self.assertEqual(response.json()["change_due"], "200.00")
        self.assertEqual(sum(i.quantity for i in Inventory.objects.filter(product=self.product)), 27)

        cart = Cart.objects.get(pk=cart_id)
        self.assertEqual(cart.status, CartStatus.CHECKED_OUT)
        self.assertEqual(cart.sale_id, sale.pk)
        self.assertFalse(CartItem.objects.filter(cart=cart).exists())

        retry = self.client.post(url, {"payment": {"method": "cash", "amount": "500.00"}}, format="json")
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(retry.json()["id"], response.json()["id"])
        self.assertEqual(Sale.objects.count(), 1)
        self.assertEqual(self._add(cart_id).json()["code"], "CART_CLOSED")
        self.assertNotEqual(self._cart(), cart_id)  # a fresh till cart

    def test_carton_checkout_charges_carton_price(self):
        cart_id = self._ready_cart(quantity=1)
        CartItem.objects.filter(cart_id=cart_id).delete()
        self._add(cart_id, quantity=2, unit_name="carton")
        response = self.client.post(f"{self.base}{cart_id}/checkout/", {}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(Decimal(response.json()["total"]), Decimal("2400.00"))
        self.assertEqual(sum(i.quantity for i in Inventory.objects.filter(product=self.product)), 6)

    def test_blocked_checkouts_leave_cart_open(self):
        cart_id = self._cart()
        url = f"{self.base}{cart_id}/checkout/"
        self.assertEqual(self.client.post(url, {}, format="json").json()["code"], "CART_EMPTY")
        self.client.patch(f"{self.base}{cart_id}/", {"customer_id": str(self.customer.pk)}, format="json")

        self._add(cart_id, quantity=31)  # more than in stock
        response = self.client.post(url, {}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Sale.objects.count(), 0)
        self.assertEqual(Cart.objects.get(pk=cart_id).status, CartStatus.ACTIVE)
        self.assertEqual(sum(i.quantity for i in Inventory.objects.filter(product=self.product)), 30)

    def test_rights_are_checked_again_at_checkout(self):
        cart_id = self._ready_cart()
        CartItem.objects.filter(cart_id=cart_id).update(discount=Decimal("10.00"))
        Cart.objects.filter(pk=cart_id).update(created_by=self.rep)
        response = self._as(self.rep).post(f"{self.base}{cart_id}/checkout/", {}, format="json")
        self.assertEqual(response.json()["code"], "DISCOUNT_NOT_ALLOWED")


class ExpiryTests(CartTestBase):
    def test_idle_carts_expire_then_are_purged(self):
        idle = self._cart()
        fresh = self._cart(user=self.rep)
        Cart.objects.filter(pk=idle).update(last_activity_at=timezone.now() - timedelta(days=8))
        self.assertEqual(expire_idle_carts(), {"expired": 1, "purged": 0})
        self.assertEqual(Cart.objects.get(pk=idle).status, CartStatus.ABANDONED)
        self.assertEqual(Cart.objects.get(pk=fresh).status, CartStatus.ACTIVE)

        self.assertEqual(expire_idle_carts(now=timezone.now() + timedelta(days=31))["purged"], 1)
        self.assertFalse(Cart.objects.filter(pk=idle).exists())
