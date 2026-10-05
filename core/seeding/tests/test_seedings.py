from django.conf import settings
from django.test import TestCase

from core.applications.subscriptions.default import DEFAULT_PLAN_NAME
from core.applications.subscriptions.default import FEATURES
from core.applications.subscriptions.models import Plan
from core.applications.users.defaults import DEFAULT_ROLE_PERMISSIONS
from core.applications.users.defaults import DEFAULT_ROLES
from core.applications.users.defaults import MODULE_BY_CODE
from core.applications.users.defaults import NIGERIAN_STATES
from core.applications.users.models import Permission
from core.applications.users.models import State
from core.helper.enums import PermissionCode
from core.seeding.registry import ordered_seeds
from core.seeding.registry import run_seeds

SKIP = set(getattr(settings, "SEEDING_SKIP", ()))


class SeedingTests(TestCase):
    def test_migrate_leaves_the_catalog_in_place(self):
        self.assertEqual(Permission.objects.count(), len(PermissionCode))
        self.assertEqual(State.objects.filter(country="NG").count(), len(NIGERIAN_STATES))
        self.assertTrue(Plan.objects.filter(name__iexact=DEFAULT_PLAN_NAME).exists())

    def test_a_second_run_changes_nothing(self):
        run_seeds(skip=SKIP)
        for name, result in run_seeds(skip=SKIP).items():
            self.assertEqual((result.created, result.updated), (0, 0), name)

    def test_code_owned_rows_are_restored(self):
        Permission.objects.filter(code="VIEW_SALES").update(name="tampered")
        run_seeds(only=["permissions"])
        self.assertEqual(Permission.objects.get(code="VIEW_SALES").name, "View Sales")

    def test_business_owned_plan_is_never_overwritten(self):
        Plan.objects.filter(name__iexact=DEFAULT_PLAN_NAME).update(max_users=99)
        run_seeds(only=["default_plan"])
        self.assertEqual(Plan.objects.get(name__iexact=DEFAULT_PLAN_NAME).max_users, 99)

    def test_every_permission_code_has_a_module(self):
        self.assertFalse(set(PermissionCode.values) - set(MODULE_BY_CODE))

    def test_role_matrix_only_names_real_roles_and_codes(self):
        slugs = {slug for slug, _name in DEFAULT_ROLES}
        self.assertFalse(set(DEFAULT_ROLE_PERMISSIONS) - slugs)
        for slug, grant in DEFAULT_ROLE_PERMISSIONS.items():
            if grant != "*":
                self.assertFalse({str(c) for c in grant} - set(PermissionCode.values), slug)

    def test_seed_graph_is_valid(self):
        names = [seed.name for seed in ordered_seeds()]
        self.assertLess(names.index("features"), names.index("default_plan"))

    def test_custom_templates_feature_is_defined(self):
        self.assertIn("CUSTOM_TEMPLATES", {feature["code"] for feature in FEATURES})
