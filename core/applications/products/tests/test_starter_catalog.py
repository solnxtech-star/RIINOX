"""Starter categories and brands by business type (products/catalog.py)."""

import uuid
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase

from core.applications.inventory.tests.factories import OrganizationFactory
from core.applications.products import catalog
from core.applications.products.models import StarterCatalogRecord
from core.seeding.registry import run_seeds
from core.applications.products.catalog import STARTER_BRANDS
from core.applications.products.catalog import STARTER_CATEGORIES
from core.applications.products.catalog import seed_starter_catalog
from core.applications.products.models import Brand
from core.applications.products.models import Category
from core.applications.users import services
from core.applications.users.models import BusinessType
from core.applications.users.models import OrganizationBusinessType
from core.applications.users.tests.factories import UserFactory


def names(model, organization):
    return set(model.objects.filter(organization=organization).values_list("name", flat=True))


class StarterCatalogTests(TestCase):
    def setUp(self):
        self.org = OrganizationFactory()

    def test_adds_the_business_types_categories_and_brands(self):
        result = seed_starter_catalog(self.org, ["pharmacy"])
        self.assertEqual(names(Category, self.org), set(STARTER_CATEGORIES["pharmacy"]))
        self.assertEqual(names(Brand, self.org), set(STARTER_BRANDS["pharmacy"]))
        self.assertEqual(result.categories, len(STARTER_CATEGORIES["pharmacy"]))

    def test_running_twice_changes_nothing(self):
        seed_starter_catalog(self.org, ["retail"])
        result = seed_starter_catalog(self.org, ["retail"])
        self.assertEqual((result.categories, result.brands), (0, 0))

    def test_keeps_the_organizations_own_rows_and_skips_names_case_insensitively(self):
        Category.objects.create(organization=self.org, name="BEVERAGES", is_active=False)
        seed_starter_catalog(self.org, ["retail"])
        beverages = Category.objects.filter(organization=self.org, name__iexact="beverages")
        self.assertEqual(beverages.count(), 1)
        self.assertFalse(beverages.get().is_active)  # never reactivated

    def test_several_business_types_merge_without_duplicates(self):
        seed_starter_catalog(self.org, ["retail", "supermarket"])
        expected = set(STARTER_CATEGORIES["retail"]) | set(STARTER_CATEGORIES["supermarket"])
        self.assertEqual(names(Category, self.org), expected)

    def test_unknown_business_type_or_one_without_brands_is_fine(self):
        result = seed_starter_catalog(self.org, ["fashion", "not-a-type"])
        self.assertEqual(result.brands, 0)
        self.assertEqual(names(Category, self.org), set(STARTER_CATEGORIES["fashion"]))

    def test_other_organizations_are_untouched(self):
        other = OrganizationFactory()
        seed_starter_catalog(self.org, ["retail"])
        self.assertFalse(Category.objects.filter(organization=other).exists())


class StarterCatalogHookTests(TestCase):
    def test_new_organization_gets_its_business_types_catalog(self):
        profile = services.OrganizationProfile(
            name="Okafor Pharmacy", country="NG", currency="NGN", state="Lagos",
            address="1 Allen Avenue", phone="+2348012345678", staff_size="",
        )
        with mock.patch.object(services, "provision_organization_documents"):
            organization = services.create_organization(
                user=UserFactory(), business_type=BusinessType.objects.get(code="pharmacy"), profile=profile,
            )
        self.assertEqual(names(Category, organization), set(STARTER_CATEGORIES["pharmacy"]))
        self.assertEqual(names(Brand, organization), set(STARTER_BRANDS["pharmacy"]))

    def test_adding_a_business_type_adds_its_catalog(self):
        org = OrganizationFactory()
        retail = BusinessType.objects.get(code="retail")
        fashion = BusinessType.objects.get(code="fashion")
        OrganizationBusinessType.objects.create(organization=org, business_type=retail, is_primary=True)

        services.set_business_types(organization=org, business_types=[retail, fashion], primary=retail)

        expected = set(STARTER_CATEGORIES["retail"]) | set(STARTER_CATEGORIES["fashion"])
        self.assertEqual(names(Category, org), expected)

    def test_backfill_command(self):
        org = OrganizationFactory()
        link(org, "supermarket")
        out = StringIO()
        call_command("seed_product_catalog", "--organization", str(org.pk), stdout=out)
        self.assertEqual(names(Category, org), set(STARTER_CATEGORIES["supermarket"]))
        call_command("seed_product_catalog", stdout=out)  # idempotent
        self.assertEqual(Category.objects.filter(organization=org).count(), len(STARTER_CATEGORIES["supermarket"]))


def link(organization, code, *, primary=True):
    return OrganizationBusinessType.objects.create(
        organization=organization, business_type=BusinessType.objects.get(code=code), is_primary=primary
    )


class StarterCatalogOnMigrateTests(TestCase):
    """The 'product_catalog' seed runs after every `migrate`."""

    def test_migrate_seed_fills_existing_organizations(self):
        org = OrganizationFactory()
        link(org, "pharmacy")
        results = run_seeds(only=["product_catalog"])
        self.assertEqual(results["product_catalog"].created, 1)
        self.assertEqual(names(Brand, org), set(STARTER_BRANDS["pharmacy"]))
        record = StarterCatalogRecord.objects.get(organization=org)
        self.assertEqual(record.categories_added, len(STARTER_CATEGORIES["pharmacy"]))

    def test_each_pair_is_applied_once_so_deletions_stick(self):
        org = OrganizationFactory()
        link(org, "retail")
        run_seeds(only=["product_catalog"])
        Category.objects.filter(organization=org, name="Stationery").delete()
        Brand.objects.filter(organization=org, name="Peak").update(name="Peak Milk")

        self.assertEqual(run_seeds(only=["product_catalog"])["product_catalog"].created, 0)
        self.assertNotIn("Stationery", names(Category, org))
        self.assertNotIn("Peak", names(Brand, org))

    def test_removing_and_re_adding_a_business_type_does_not_reseed(self):
        org = OrganizationFactory()
        retail, fashion = (BusinessType.objects.get(code=c) for c in ("retail", "fashion"))
        link(org, "retail")
        services.set_business_types(organization=org, business_types=[retail, fashion], primary=retail)
        Category.objects.filter(organization=org, name="Footwear").delete()

        services.set_business_types(organization=org, business_types=[retail], primary=retail)
        services.set_business_types(organization=org, business_types=[retail, fashion], primary=retail)
        self.assertNotIn("Footwear", names(Category, org))

    def test_inactive_organizations_are_skipped(self):
        org = OrganizationFactory(is_active=False)
        link(org, "retail")
        run_seeds(only=["product_catalog"])
        self.assertFalse(Category.objects.filter(organization=org).exists())

    def test_one_failing_organization_does_not_block_the_others(self):
        bad, good = OrganizationFactory(), OrganizationFactory()
        link(bad, "retail")
        link(good, "retail")
        real = catalog.apply_starter_catalog

        def flaky(organization, business_types):
            if uuid.UUID(str(organization.pk)) == uuid.UUID(str(bad.pk)):
                raise RuntimeError("boom")
            return real(organization, business_types)

        with mock.patch.object(catalog, "apply_starter_catalog", side_effect=flaky):
            result = catalog.backfill_starter_catalogs()
        self.assertEqual((result.applied, result.failed), (1, 1))
        self.assertTrue(Category.objects.filter(organization=good).exists())
        self.assertFalse(StarterCatalogRecord.objects.filter(organization=bad).exists())  # retried next run
