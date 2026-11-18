"""Starter categories and brands by business type (products/catalog.py)."""

from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase

from core.applications.inventory.tests.factories import OrganizationFactory
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

    def test_adding_a_business_type_adds_its_catalog_only(self):
        org = OrganizationFactory()
        retail = BusinessType.objects.get(code="retail")
        fashion = BusinessType.objects.get(code="fashion")
        OrganizationBusinessType.objects.create(organization=org, business_type=retail, is_primary=True)

        services.set_business_types(organization=org, business_types=[retail, fashion], primary=retail)

        # fashion was new: its categories arrive; retail was already there: not re-added.
        self.assertEqual(names(Category, org), set(STARTER_CATEGORIES["fashion"]))

    def test_backfill_command(self):
        org = OrganizationFactory()
        OrganizationBusinessType.objects.create(
            organization=org, business_type=BusinessType.objects.get(code="supermarket"), is_primary=True
        )
        out = StringIO()
        call_command("seed_product_catalog", "--organization", str(org.pk), stdout=out)
        self.assertEqual(names(Category, org), set(STARTER_CATEGORIES["supermarket"]))
        call_command("seed_product_catalog", "--all", stdout=out)  # idempotent
        self.assertEqual(Category.objects.filter(organization=org).count(), len(STARTER_CATEGORIES["supermarket"]))
