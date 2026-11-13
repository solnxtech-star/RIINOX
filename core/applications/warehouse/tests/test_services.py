from __future__ import annotations

import importlib
import uuid
from types import SimpleNamespace

from django.apps import apps
from django.test import TestCase

from core.applications.inventory.tests.factories import OrganizationFactory
from core.applications.notification.audit.action import AuditAction
from core.applications.notification.models import AuditLog
from core.applications.subscriptions.services.usage import get_usage
from core.applications.warehouse.models import Warehouse
from core.applications.warehouse.services import create_default_location
from core.applications.warehouse.services import create_warehouse
from core.applications.warehouse.services import deactivate_warehouse
from core.applications.warehouse.services import delete_warehouse
from core.applications.warehouse.services import set_default_location
from core.helper.custom_exceptions import BusinessRuleError
from core.helper.enums import LocationType
from core.helper.enums import UsageMetric

backfill = importlib.import_module(
    "core.applications.warehouse.migrations.0003_backfill_default_locations"
)


def _org(max_locations=10):
    organization = OrganizationFactory()
    plan = organization.subscription.plan
    plan.max_locations = max_locations
    plan.save(update_fields=["max_locations"])
    return organization


def _same_pk(a, b) -> bool:
    """New instances carry a hex-string pk, fetched rows a UUID."""
    return uuid.UUID(str(a)) == uuid.UUID(str(b))


def _locations_used(organization) -> int:
    return get_usage(organization, UsageMetric.LOCATIONS)


class CreateDefaultLocationTests(TestCase):
    def test_creates_counted_default_with_type_from_business(self):
        org = _org()
        location = create_default_location(
            organization=org, business_type=SimpleNamespace(code="retail")
        )

        self.assertTrue(location.is_default)
        self.assertEqual(location.code, "MAIN")
        self.assertEqual(location.location_type, LocationType.STORE)
        self.assertEqual(_locations_used(org), 1)
        self.assertTrue(
            AuditLog.objects.filter(action=AuditAction.LOCATION_CREATED, resource_id=str(location.pk)).exists()
        )

    def test_is_idempotent(self):
        org = _org()
        first = create_default_location(organization=org)
        second = create_default_location(organization=org)

        self.assertTrue(_same_pk(first.pk, second.pk))
        self.assertEqual(Warehouse.objects.filter(organization=org).count(), 1)
        self.assertEqual(_locations_used(org), 1)

    def test_picks_free_code_when_main_is_taken(self):
        org = _org()
        create_warehouse(organization=org, name="Old main", code="main")

        location = create_default_location(organization=org)

        self.assertEqual(location.code, "MAIN-2")

    def test_counts_against_plan_limit(self):
        org = _org(max_locations=1)
        create_default_location(organization=org)

        with self.assertRaises(BusinessRuleError) as ctx:
            create_warehouse(organization=org, name="Second", code="SEC")
        self.assertEqual(ctx.exception.code, "PLAN_LIMIT_REACHED")


class DefaultLocationProtectionTests(TestCase):
    def setUp(self):
        self.org = _org()
        self.default = create_default_location(organization=self.org)

    def test_default_cannot_be_deactivated(self):
        with self.assertRaises(BusinessRuleError) as ctx:
            deactivate_warehouse(self.default)

        self.assertEqual(ctx.exception.code, "DEFAULT_LOCATION_PROTECTED")
        self.default.refresh_from_db()
        self.assertTrue(self.default.is_active)
        self.assertEqual(_locations_used(self.org), 1)

    def test_default_cannot_be_deleted(self):
        with self.assertRaises(BusinessRuleError) as ctx:
            delete_warehouse(warehouse=self.default)

        self.assertEqual(ctx.exception.code, "DEFAULT_LOCATION_PROTECTED")
        self.assertTrue(Warehouse.objects.filter(pk=self.default.pk).exists())
        self.assertEqual(_locations_used(self.org), 1)

    def test_other_location_delete_releases_usage(self):
        other = create_warehouse(organization=self.org, name="Branch 2", code="B2")
        self.assertEqual(_locations_used(self.org), 2)

        delete_warehouse(warehouse=other)

        self.assertEqual(_locations_used(self.org), 1)

    def test_delete_uses_current_row_state_not_stale_instance(self):
        other = create_warehouse(organization=self.org, name="Branch 2", code="B2")
        stale = Warehouse.objects.get(pk=other.pk)
        deactivate_warehouse(other)  # usage back to 1

        delete_warehouse(warehouse=stale)  # stale.is_active is still True

        self.assertEqual(_locations_used(self.org), 1)


class SetDefaultLocationTests(TestCase):
    def setUp(self):
        self.org = _org()
        self.default = create_default_location(organization=self.org)
        self.other = create_warehouse(organization=self.org, name="Branch 2", code="B2")

    def test_switches_default_and_audits(self):
        set_default_location(location=self.other)

        self.default.refresh_from_db()
        self.other.refresh_from_db()
        self.assertFalse(self.default.is_default)
        self.assertTrue(self.other.is_default)
        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditAction.LOCATION_DEFAULT_CHANGED, resource_id=str(self.other.pk)
            ).exists()
        )
        # The old default is now an ordinary location and can be retired.
        deactivate_warehouse(self.default)

    def test_refuses_inactive_location(self):
        deactivate_warehouse(self.other)

        with self.assertRaises(BusinessRuleError) as ctx:
            set_default_location(location=self.other)

        self.assertEqual(ctx.exception.code, "LOCATION_INACTIVE")
        self.default.refresh_from_db()
        self.assertTrue(self.default.is_default)

    def test_already_default_is_a_no_op(self):
        result = set_default_location(location=self.default)

        self.assertTrue(result.is_default)
        self.assertFalse(AuditLog.objects.filter(action=AuditAction.LOCATION_DEFAULT_CHANGED).exists())


class BackfillDefaultLocationsTests(TestCase):
    def test_promotes_oldest_active_or_creates_main(self):
        with_locations = _org()
        oldest = create_warehouse(organization=with_locations, name="A", code="A")
        create_warehouse(organization=with_locations, name="B", code="B")

        only_inactive = _org()
        retired = create_warehouse(organization=only_inactive, name="Old", code="MAIN")
        deactivate_warehouse(retired)

        empty = _org()

        already = _org()
        existing_default = create_default_location(organization=already)

        backfill.backfill_default_locations(apps, None)
        backfill.backfill_default_locations(apps, None)  # re-running changes nothing

        # Promoted: no new rows, usage unchanged.
        oldest.refresh_from_db()
        self.assertTrue(oldest.is_default)
        self.assertEqual(Warehouse.objects.filter(organization=with_locations).count(), 2)
        self.assertEqual(_locations_used(with_locations), 2)

        # Only inactive locations: a new Main Branch, with a free code, counted.
        created = Warehouse.objects.get(organization=only_inactive, is_default=True)
        self.assertEqual(created.code, "MAIN-2")
        self.assertTrue(created.is_active)
        self.assertEqual(_locations_used(only_inactive), 1)

        # No locations at all: created and counted.
        self.assertEqual(Warehouse.objects.filter(organization=empty, is_default=True).count(), 1)
        self.assertEqual(_locations_used(empty), 1)

        # Untouched.
        self.assertTrue(
            _same_pk(Warehouse.objects.get(organization=already, is_default=True).pk, existing_default.pk)
        )
        self.assertEqual(_locations_used(already), 1)
