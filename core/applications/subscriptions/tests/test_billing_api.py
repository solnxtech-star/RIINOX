"""
Billing API end to end with the fake provider: catalogue, entitlements, billing
page, checkout -> provider webhook -> plan activated, cancellation, the webhook's
security, error codes and the scheduled jobs.
"""

from __future__ import annotations

import pathlib
import re

from django.core.cache import cache
from django.test import TestCase
from django.test import override_settings
from rest_framework.test import APIClient

from core.applications.inventory.tests.factories import OrganizationFactory
from core.applications.subscriptions import tasks
from core.applications.subscriptions.errors import _SPECS
from core.applications.subscriptions.errors import SubscriptionErrorCode
from core.applications.subscriptions.models import Plan
from core.applications.subscriptions.models import PlanPrice
from core.applications.subscriptions.providers.registry import get_provider
from core.applications.users.models import Membership
from core.applications.users.services import seed_default_roles
from core.applications.users.tests.factories import UserFactory

BILLING_SETTINGS = {
    "BILLING_PROVIDER_BY_CURRENCY": {"*": "fake"},
    "BILLING_ALLOW_FAKE_PROVIDER": True,
    "BILLING_ALLOWED_CALLBACK_HOSTS": ["app.example.com"],
    "BILLING_WEBHOOK_PROCESS_INLINE": True,
}
CALLBACK = "https://app.example.com/billing/done"


def _errors(response) -> str:
    return str(response.content)


class SubscriptionErrorCodeTests(TestCase):
    def test_every_code_used_is_defined_with_a_message(self):
        # Undefined codes used to raise AttributeError (a 500) on every billing error path.
        root = pathlib.Path(__file__).resolve().parents[1]
        used = set()
        for path in root.rglob("*.py"):
            used |= set(re.findall(r"Err\.([A-Z_]+)", path.read_text(encoding="utf-8")))
        defined = {code.name for code in SubscriptionErrorCode}
        self.assertEqual(used - defined, set())
        self.assertEqual({code for code in SubscriptionErrorCode} - set(_SPECS), set())


@override_settings(**BILLING_SETTINGS)
class BillingAPITests(TestCase):
    def setUp(self):
        cache.clear()  # entitlements are cached per organization
        self.org = OrganizationFactory()  # NGN, starts on the default free plan
        self.roles = seed_default_roles(self.org)
        self.owner = UserFactory()
        Membership.objects.create(user=self.owner, organization=self.org, role=self.roles["owner"])
        self.staff = UserFactory()
        Membership.objects.create(user=self.staff, organization=self.org, role=self.roles["inventory-staff"])

        self.pro = Plan.objects.create(
            code="pro", name="Pro", is_public=True, is_active=True, sort_order=10,
            max_users=20, max_products=None, max_locations=5, max_transactions_per_month=None,
        )
        PlanPrice.objects.create(plan=self.pro, currency="NGN", interval="monthly", amount_minor=1_500_000)
        PlanPrice.objects.create(plan=self.pro, currency="USD", interval="monthly", amount_minor=1_000)

        self.client = APIClient()
        self.base = f"/api/organizations/{self.org.pk}/billing"

    def _as(self, user):
        self.client.force_authenticate(user)
        return self.client

    # --- reads ------------------------------------------------------------ #
    def test_plan_catalogue_filters_prices_by_currency(self):
        response = self._as(self.owner).get("/api/billing/plans/?currency=ngn")

        self.assertEqual(response.status_code, 200, _errors(response))
        pro = next(plan for plan in response.data if plan["code"] == "pro")
        self.assertEqual([p["currency"] for p in pro["prices"]], ["NGN"])
        self.assertEqual(pro["prices"][0]["amount"], "15000.00")
        self.assertIsNone(pro["limits"]["products"])  # unlimited

    def test_entitlements_for_any_member_and_404_for_outsiders(self):
        member = self._as(self.staff).get(f"{self.base}/entitlements/")
        outsider = self._as(UserFactory()).get(f"{self.base}/entitlements/")

        self.assertEqual(member.status_code, 200, _errors(member))
        self.assertEqual(member.data["access"], "full")
        self.assertIn("limits", member.data)
        self.assertEqual(outsider.status_code, 404)

    def test_billing_page_is_owner_and_admin_only(self):
        staff = self._as(self.staff).get(f"{self.base}/")
        owner = self._as(self.owner).get(f"{self.base}/")

        self.assertEqual(staff.status_code, 403)
        self.assertEqual(owner.status_code, 200, _errors(owner))
        self.assertIn("locations", owner.data["usage"])
        self.assertFalse(owner.data["cancel_at_period_end"])

    # --- checkout --------------------------------------------------------- #
    def _checkout(self, **overrides):
        body = {"plan": "pro", "interval": "monthly", "callback_url": CALLBACK, "idempotency_key": "attempt-1"}
        return self._as(self.owner).post(f"{self.base}/checkout/", {**body, **overrides}, format="json")

    def test_checkout_returns_provider_url_and_is_idempotent(self):
        first = self._checkout()
        again = self._checkout()

        self.assertEqual(first.status_code, 201, _errors(first))
        self.assertTrue(first.data["authorization_url"].startswith("https://fake.test/pay/"))
        self.assertEqual(again.data["reference"], first.data["reference"])

    def test_checkout_rejects_unlisted_callback_host(self):
        response = self._checkout(callback_url="https://evil.example.net/steal", idempotency_key="x")

        self.assertEqual(response.status_code, 400, _errors(response))
        self.assertIn("INVALID_CALLBACK_URL", _errors(response))

    def test_checkout_unknown_plan(self):
        response = self._checkout(plan="does-not-exist")

        self.assertIn("PLAN_NOT_AVAILABLE", _errors(response))

    def test_full_upgrade_flow_checkout_webhook_activates_plan(self):
        reference = self._checkout().data["reference"]
        provider = get_provider("fake")
        provider.settle(reference)
        body, headers = provider.build_webhook(provider.verify_transaction(reference))

        with self.captureOnCommitCallbacks(execute=True):
            webhook = APIClient().post(
                "/api/billing/webhooks/fake/", body, content_type="application/json",
                HTTP_X_FAKE_SIGNATURE=headers["x-fake-signature"],
            )

        self.assertEqual(webhook.status_code, 200, _errors(webhook))
        entitlements = self._as(self.owner).get(f"{self.base}/entitlements/")
        self.assertEqual(entitlements.data["plan_code"], "pro")

    # --- webhook security ------------------------------------------------- #
    def test_webhook_with_bad_signature_is_401(self):
        response = APIClient().post(
            "/api/billing/webhooks/fake/", b"{}", content_type="application/json",
            HTTP_X_FAKE_SIGNATURE="forged",
        )

        self.assertEqual(response.status_code, 401)

    def test_webhook_for_unknown_provider_is_404(self):
        response = APIClient().post("/api/billing/webhooks/nope/", b"{}", content_type="application/json")

        self.assertEqual(response.status_code, 404)

    # --- lifecycle -------------------------------------------------------- #
    def test_cancel_without_paid_plan_is_409_not_500(self):
        response = self._as(self.owner).post(f"{self.base}/cancel/")

        self.assertEqual(response.status_code, 409, _errors(response))
        self.assertIn("SUBSCRIPTION_INVALID_STATE", _errors(response))


class BillingTaskTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_scheduled_tasks_run(self):
        for task in (tasks.run_renewals, tasks.run_period_ends, tasks.run_expirations,
                     tasks.run_reconciliation, tasks.process_webhook_backlog):
            self.assertIsInstance(task(), dict)

    def test_overlapping_run_is_skipped(self):
        cache.add("billing:job-lock:renewals", "1", timeout=60)

        self.assertEqual(tasks.run_renewals(), {"skipped_overlap": 1})

    def test_beat_schedule_points_at_registered_tasks(self):
        from django.conf import settings

        from config.celery_app import app

        registered = set(app.tasks)
        for entry in settings.CELERY_BEAT_SCHEDULE.values():
            self.assertIn(entry["task"], registered)
