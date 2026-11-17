"""
Notification settings and delivery (PRD §33).

Setup: Lekki and Ikeja. owner (all branches), admin (all), manager (all),
inv_lekki (inventory manager, Lekki only), inv_ikeja (inventory manager, Ikeja
only), staff (inventory staff, Lekki). Coca-Cola: minimum 10, 12 at Lekki.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.core import mail
from django.core.cache import cache
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from core.applications.customers.models import Customer
from core.applications.inventory.models import Inventory
from core.applications.inventory.services import approve_stock_adjustment
from core.applications.inventory.services import create_opening_stock
from core.applications.inventory.services import request_stock_adjustment
from core.applications.inventory.tests.factories import OrganizationFactory
from core.applications.inventory.tests.factories import ProductFactory
from core.applications.notification.models import Notification
from core.applications.notification.preferences import update_settings
from core.applications.notification.tasks import scan_expiring_batches
from core.applications.products.models import ProductBatch
from core.applications.sales.service import create_sale
from core.applications.users.invitation_services import update_membership_locations
from core.applications.users.models import Membership
from core.applications.users.models import MembershipLocation
from core.applications.users.services import seed_default_roles
from core.applications.users.tests.factories import UserFactory
from core.applications.warehouse.models import Warehouse
from core.helper.org_time import organization_today


class NotificationTestBase(TestCase):
    """Shared setup and helpers; no tests of its own."""

    def setUp(self):
        cache.clear()
        self.org = OrganizationFactory()
        self.org.default_tax_rate = Decimal("0")
        self.org.save(update_fields=["default_tax_rate"])
        plan = self.org.subscription.plan
        plan.max_locations = None
        plan.max_transactions_per_month = None
        plan.save(update_fields=["max_locations", "max_transactions_per_month"])
        self.roles = seed_default_roles(self.org)

        self.lekki = Warehouse.objects.create(organization=self.org, name="Lekki", code="LEK", is_default=True)
        self.ikeja = Warehouse.objects.create(organization=self.org, name="Ikeja", code="IKJ")

        self.owner = self._member("owner")
        self.admin = self._member("administrator")
        self.manager = self._member("manager")
        self.inv_lekki = self._member("inventory-manager", self.lekki)
        self.inv_ikeja = self._member("inventory-manager", self.ikeja)
        self.staff = self._member("inventory-staff", self.lekki)

        self.customer = Customer.objects.create(organization=self.org, name="ABC Stores", email="abc@x.test")
        self.product = ProductFactory(organization=self.org, name="Coca-Cola", purchase_cost=Decimal("150"),
                                      customer_sale_price=Decimal("250"), track_inventory=True,
                                      minimum_stock_level=10)
        create_opening_stock(self.product, None, None, self.lekki, 12, self.owner)
        self.client = APIClient()
        mail.outbox.clear()

    def _member(self, role_slug, location=None):
        user = UserFactory()
        membership = Membership.objects.create(
            user=user, organization=self.org, role=self.roles[role_slug], all_locations=location is None
        )
        if location is not None:
            MembershipLocation.objects.create(membership=membership, location=location)
        return user

    def _sale(self, quantity):
        with self.captureOnCommitCallbacks(execute=True):
            return create_sale(
                {"organization": self.org, "customer": self.customer, "warehouse": self.lekki,
                 "sales_rep": self.owner,
                 "items": [{"product": self.product, "product_name": "Coca-Cola", "quantity": quantity,
                            "unit_price": Decimal("250.00")}]},
                actor=self.owner,
            )

    def _recipients(self, event):
        return set(Notification.objects.filter(notification_type=event).values_list("recipient_id", flat=True))


class NotificationTests(NotificationTestBase):
    # delivery ----------------------------------------------------------- #
    def test_low_stock_goes_to_roles_with_branch_access_and_is_deduplicated(self):
        self._sale(3)  # 12 -> 9, minimum 10

        self.assertEqual(self._recipients("low_stock"), {self.manager.pk, self.inv_lekki.pk})
        note = Notification.objects.filter(notification_type="low_stock").first()
        self.assertIn("Coca-Cola is low at Lekki: 9 left", note.message)
        self.assertEqual(note.link["query"]["low_stock"], "true")
        self.assertEqual(sorted(m.to[0] for m in mail.outbox), sorted([self.manager.email, self.inv_lekki.email]))

        self._sale(1)  # still low: no repeat within 24 hours
        self.assertEqual(Notification.objects.filter(notification_type="low_stock").count(), 2)

    def test_out_of_stock_and_disabled_events(self):
        with self.captureOnCommitCallbacks(execute=True):
            update_settings(self.org, actor=self.owner, changes=[{"event": "low_stock", "enabled": False}])
        self._sale(3)
        self.assertFalse(Notification.objects.filter(notification_type="low_stock").exists())

        self._sale(9)  # -> 0
        self.assertEqual(self._recipients("out_of_stock"), {self.manager.pk, self.inv_lekki.pk})

    def test_email_switch_and_recipient_roles(self):
        with self.captureOnCommitCallbacks(execute=True):
            update_settings(self.org, actor=self.owner, changes=[
                {"event": "low_stock", "email": False, "recipient_role_ids": [str(self.roles["owner"].pk)]},
            ])
        self._sale(3)
        self.assertEqual(self._recipients("low_stock"), {self.owner.pk})
        self.assertEqual(mail.outbox, [])

    def test_large_sale_threshold(self):
        with self.captureOnCommitCallbacks(execute=True):
            update_settings(self.org, actor=self.owner,
                            changes=[{"event": "large_sale", "options": {"threshold": 500}}])
        self._sale(1)  # 250: below
        self.assertFalse(Notification.objects.filter(notification_type="large_sale").exists())
        self._sale(2)  # 500: large. The owner made it, so only the manager is told.
        self.assertEqual(self._recipients("large_sale"), {self.manager.pk})

    def test_approval_flow(self):
        with self.captureOnCommitCallbacks(execute=True):
            adjustment = request_stock_adjustment(
                product=self.product, warehouse=self.lekki, quantity_change=-1, user=self.staff
            )
        # Owner, admin and the Lekki inventory manager; not Ikeja's, not the requester.
        self.assertEqual(self._recipients("pending_approval"), {self.owner.pk, self.admin.pk, self.inv_lekki.pk})

        with self.captureOnCommitCallbacks(execute=True):
            approve_stock_adjustment(adjustment.pk, self.inv_lekki)
        self.assertIn(self.staff.pk, self._recipients("approval_decision"))
        self.assertNotIn(self.inv_lekki.pk, self._recipients("approval_decision"))

    def test_permission_change(self):
        membership = Membership.objects.get(user=self.staff, organization=self.org)
        with self.captureOnCommitCallbacks(execute=True):
            update_membership_locations(membership=membership, actor=self.owner, all_locations=False,
                                        locations=[self.ikeja])
        self.assertEqual(self._recipients("permission_change"), {self.admin.pk})
        self.assertIn("branch access set to Ikeja", Notification.objects.get(recipient=self.admin).message)

    def test_failed_logins_alert_owners_and_admins_once(self):
        UserFactory(email="cashier@x.test", password="Correct-Horse-1!")
        cashier = type(self.owner).objects.get(email="cashier@x.test")
        Membership.objects.create(user=cashier, organization=self.org, role=self.roles["sales-representative"])

        # The login view runs outside the request transaction, so in production a 401
        # rolls nothing back. Here the whole test is one transaction, and DRF's handler
        # would mark it for rollback after the first 401; neutralise that for this test.
        with mock.patch("rest_framework.views.set_rollback"), self.captureOnCommitCallbacks(execute=True):
            for _ in range(6):
                response = self.client.post(
                    "/api/auth/jwt/login/", {"email": "cashier@x.test", "password": "nope"}, format="json"
                )
                self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED, response.data)
        self.assertEqual(self._recipients("failed_login"), {self.owner.pk, self.admin.pk})
        self.assertEqual(Notification.objects.filter(notification_type="failed_login").count(), 2)

    def test_daily_expiry_scan(self):
        batch = ProductBatch.objects.create(product=self.product, batch_number="B1",
                                            expiry_date=organization_today(self.org) + timedelta(days=10))
        Inventory.objects.create(product=self.product, batch=batch, warehouse=self.lekki, quantity=5)

        scan_expiring_batches()
        scan_expiring_batches()  # same day: no duplicate
        notes = Notification.objects.filter(notification_type="batch_expiry")
        self.assertEqual({n.recipient_id for n in notes}, {self.inv_lekki.pk})
        self.assertIn("1 product is expiring within 30 days at Lekki", notes.first().message)

    # settings API ------------------------------------------------------- #
    def test_settings_api(self):
        url = f"/api/organizations/{self.org.pk}/notification-settings/"
        self.client.force_authenticate(self.owner)
        data = self.client.get(url).data
        self.assertEqual([c["category"] for c in data["categories"]], ["inventory", "sales", "security", "operations"])
        events = {e["event"]: e for c in data["categories"] for e in c["events"]}
        self.assertEqual(len(events), 13)
        sales = next(c for c in data["categories"] if c["category"] == "sales")
        self.assertIn("overdue_payment", [e["event"] for e in sales["events"]])
        self.assertEqual({r["slug"] for r in events["low_stock"]["recipient_roles"]}, {"inventory-manager", "manager"})
        self.assertFalse(events["incoming_sales_order"]["triggered"])
        self.assertFalse(events["stock_variance"]["email"])

        response = self.client.patch(url, {"settings": [
            {"event": "stock_variance", "email": True},
            {"event": "large_sale", "options": {"threshold": 750000}},
        ]}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        events = {e["event"]: e for c in response.data["categories"] for e in c["events"]}
        self.assertTrue(events["stock_variance"]["email"])
        self.assertTrue(events["stock_variance"]["customized"])
        self.assertEqual(events["large_sale"]["options"][0]["value"], 750000)

        foreign_role = seed_default_roles(OrganizationFactory())["owner"]
        for bad in (
            [{"event": "nope"}],
            [{"event": "large_sale", "options": {"threshold": 0}}],
            [{"event": "low_stock", "options": {"threshold": 5}}],
            [{"event": "low_stock", "recipient_role_ids": [str(foreign_role.pk)]}],
            [{"event": "low_stock"}, {"event": "low_stock"}],
        ):
            self.assertEqual(self.client.patch(url, {"settings": bad}, format="json").status_code,
                             status.HTTP_400_BAD_REQUEST, bad)

        self.client.force_authenticate(self.manager)  # no VIEW_SETTINGS / EDIT_SETTINGS
        self.assertEqual(self.client.get(url).status_code, status.HTTP_403_FORBIDDEN)

    def test_inbox_api(self):
        self._sale(3)
        base = f"/api/organizations/{self.org.pk}/notifications/"
        self.client.force_authenticate(self.manager)

        listed = self.client.get(base).data
        self.assertEqual(listed["count"], 1)
        note_id = listed["results"][0]["id"]
        self.assertEqual(listed["results"][0]["event"], "low_stock")
        self.assertEqual(listed["results"][0]["location"]["name"], "Lekki")
        self.assertEqual(self.client.get(f"{base}unread-count/").data, {"unread": 1})

        self.assertEqual(self.client.post(f"{base}{note_id}/read/").status_code, status.HTTP_200_OK)
        self.assertEqual(self.client.get(f"{base}unread-count/").data, {"unread": 0})
        self.assertEqual(self.client.get(base, {"unread": "true"}).data["count"], 0)

        # Someone else's notification is invisible.
        other = Notification.objects.get(recipient=self.inv_lekki)
        self.assertEqual(self.client.post(f"{base}{other.pk}/read/").status_code, status.HTTP_404_NOT_FOUND)

        self.client.force_authenticate(self.inv_lekki)
        self.assertEqual(self.client.post(f"{base}read-all/").data, {"marked_read": 1})

        self.client.force_authenticate(UserFactory())  # not a member
        self.assertEqual(self.client.get(base).status_code, status.HTTP_404_NOT_FOUND)


class PersonalPreferenceTests(NotificationTestBase):
    """Personal 'My notifications' choices on top of the organization settings."""

    def _prefs(self, user, body=None):
        self.client.force_authenticate(user)
        url = f"/api/organizations/{self.org.pk}/notification-preferences/"
        return self.client.get(url) if body is None else self.client.patch(url, body, format="json")

    def test_personal_email_and_in_app_switches(self):
        self.assertEqual(self._prefs(self.manager, {"preferences": [{"event": "low_stock", "email": False}]}).status_code,
                         status.HTTP_200_OK)
        self._prefs(self.inv_lekki, {"preferences": [{"event": "low_stock", "in_app": False}]})
        self._sale(3)

        # The manager still sees it in-app but gets no email; inv_lekki gets the email only.
        self.assertEqual(self._recipients("low_stock"), {self.manager.pk})
        self.assertEqual([m.to[0] for m in mail.outbox], [self.inv_lekki.email])

    def test_get_shows_what_reaches_me(self):
        events = {e["event"]: e for c in self._prefs(self.manager).data["categories"] for e in c["events"]}
        self.assertTrue(events["low_stock"]["receives"])
        self.assertFalse(events["failed_login"]["receives"])
        self.assertTrue(events["low_stock"]["email"])
        self.assertFalse(events["stock_variance"]["organization_email"])

    def test_security_alerts_are_locked_for_owners_and_admins(self):
        response = self._prefs(self.owner, {"preferences": [{"event": "failed_login", "in_app": False}]})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        events = {e["event"]: e for c in self._prefs(self.admin).data["categories"] for e in c["events"]}
        self.assertTrue(events["permission_change"]["locked"])
        # A muted-but-locked row (e.g. stored before) is ignored at delivery time too.
        from core.applications.notification.models import UserNotificationPreference
        UserNotificationPreference.objects.create(user=self.admin, organization=self.org, event="permission_change",
                                                  in_app=False, email=False)
        membership = Membership.objects.get(user=self.staff, organization=self.org)
        with self.captureOnCommitCallbacks(execute=True):
            update_membership_locations(membership=membership, actor=self.owner, all_locations=True)
        self.assertEqual(self._recipients("permission_change"), {self.admin.pk})

class GroupingAndOverdueTests(NotificationTestBase):
    def test_low_stock_groups_into_one_unread_row_per_person(self):
        rice = ProductFactory(organization=self.org, name="Rice 50kg", purchase_cost=Decimal("100"),
                              customer_sale_price=Decimal("200"), track_inventory=True, minimum_stock_level=10)
        create_opening_stock(rice, None, None, self.lekki, 11, self.owner)

        self._sale(3)  # Coca-Cola 12 -> 9: low
        with self.captureOnCommitCallbacks(execute=True):
            create_sale(
                {"organization": self.org, "customer": self.customer, "warehouse": self.lekki, "sales_rep": self.owner,
                 "items": [{"product": rice, "product_name": "Rice", "quantity": 2, "unit_price": Decimal("200")}]},
                actor=self.owner,
            )  # Rice 11 -> 9: low

        rows = Notification.objects.filter(recipient=self.manager, notification_type="low_stock")
        self.assertEqual(rows.count(), 1)
        group = rows.get()
        self.assertEqual(group.message, "2 products are below their reorder level")
        self.assertEqual(group.data["count"], 2)
        self.assertEqual(group.link, {"resource": "products", "query": {"low_stock": "true"}})

        # Once read, the next occurrence opens a new group.
        Notification.objects.filter(pk=group.pk).update(is_read=True)
        self._sale(9)  # Coca-Cola -> 0: out of stock (a different event)
        self.assertEqual(Notification.objects.filter(recipient=self.manager, notification_type="out_of_stock").count(), 1)

    def test_pending_approvals_group(self):
        with self.captureOnCommitCallbacks(execute=True):
            request_stock_adjustment(product=self.product, warehouse=self.lekki, quantity_change=-1, user=self.staff)
        with self.captureOnCommitCallbacks(execute=True):
            request_stock_adjustment(product=self.product, warehouse=self.lekki, quantity_change=-2, user=self.staff)

        group = Notification.objects.get(recipient=self.owner, notification_type="pending_approval")
        self.assertEqual(group.message, "2 requests are waiting for your approval")
        self.assertEqual(Notification.objects.filter(recipient=self.owner, is_read=False).count(), 1)

    def test_overdue_invoice_scan(self):
        from core.applications.invoice.models import Invoice
        from core.applications.notification.tasks import scan_overdue_invoices

        with self.captureOnCommitCallbacks(execute=True):
            sale = create_sale(
                {"organization": self.org, "customer": self.customer, "warehouse": self.lekki, "sales_rep": self.owner,
                 "items": [{"product": self.product, "product_name": "Coca-Cola", "quantity": 1,
                            "unit_price": Decimal("250")}]},
                actor=self.owner,
            )  # unpaid
        Invoice.objects.filter(sale=sale).update(due_date=organization_today(self.org) - timedelta(days=5))

        scan_overdue_invoices()
        scan_overdue_invoices()  # weekly reminder: no repeat today
        notes = Notification.objects.filter(notification_type="overdue_payment")
        self.assertEqual({n.recipient_id for n in notes}, {self.owner.pk, self.manager.pk})
        self.assertIn("ABC Stores owes ₦250", notes.first().message)
        self.assertIn("5 days overdue", notes.first().message)
