"""
Starter product categories and brands, chosen by business type.

Categories and brands belong to each organization, so they cannot be seeded
once for the whole platform. Instead a new organization starts with the
categories and brands of its business type(s), so its product form isn't empty
on day one. They are ordinary rows afterwards: the organization can rename,
deactivate or delete them.

When this runs (all through `apply_starter_catalog`):
- `create_organization` (users/services.py): the primary business type.
- `set_business_types`: any business type the organization has not received yet.
- After every `migrate` (products/seeds.py, the "product_catalog" seed): every
  organization/business type pair still missing, so existing organizations and
  restored databases are covered without a data migration.
- `manage.py seed_product_catalog`: on demand.

Each organization/business type pair is applied **once**, recorded in
`StarterCatalogRecord`. After that it is never applied again, so categories an
organization renamed, deactivated or deleted stay that way.

Within one application, only missing names are added (compared case-insensitively):
an organization that already made its own "beverages" keeps it, without a duplicate.

Edit the lists below to change what organizations get from now on. Changes are
not pushed to organizations that already received that business type's set.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass

from django.db import IntegrityError
from django.db import transaction

logger = logging.getLogger(__name__)

STARTER_CATEGORIES: dict[str, tuple[str, ...]] = {
    "retail": (
        "Groceries", "Beverages", "Snacks & Confectionery", "Toiletries & Personal Care",
        "Household & Cleaning", "Baby Products", "Stationery", "Electronics & Accessories",
    ),
    "supermarket": (
        "Groceries", "Beverages", "Dairy & Eggs", "Frozen Foods", "Fresh Produce", "Bakery",
        "Snacks & Confectionery", "Toiletries & Personal Care", "Household & Cleaning",
        "Baby Products", "Wines & Spirits",
    ),
    "restaurant_food": (
        "Main Dishes", "Rice Dishes", "Soups & Swallows", "Grills & Proteins", "Sides",
        "Drinks", "Desserts", "Raw Ingredients", "Packaging",
    ),
    "pharmacy": (
        "Prescription Medicines", "Over-the-Counter Drugs", "Antibiotics", "Pain Relief",
        "Vitamins & Supplements", "First Aid", "Medical Devices", "Personal Care",
        "Mother & Baby Care",
    ),
    "hospitality": (
        "Room Amenities", "Food & Beverage", "Minibar", "Housekeeping Supplies",
        "Linen & Towels", "Laundry", "Maintenance Supplies",
    ),
    "manufacturing": (
        "Raw Materials", "Work in Progress", "Finished Goods", "Packaging Materials",
        "Spare Parts", "Consumables",
    ),
    "wholesale_distribution": (
        "Food & Groceries", "Beverages", "Household & Cleaning", "Personal Care",
        "Building Materials", "Bulk Packaging",
    ),
    "fashion": (
        "Men's Clothing", "Women's Clothing", "Children's Clothing", "Footwear", "Bags",
        "Accessories", "Fabrics",
    ),
    "school": ("Textbooks", "Stationery", "Uniforms", "Sports Equipment", "Lab Supplies"),
    "general_trading": ("General Merchandise", "Food & Groceries", "Household", "Electronics"),
    "services": ("Service Packages", "Parts & Materials", "Consumables"),
}

_FMCG_BRANDS = (
    "Dangote", "Golden Penny", "Indomie", "Peak", "Dano", "Nestlé", "Cadbury", "Coca-Cola",
    "Pepsi", "Nigerian Breweries", "Guinness", "Unilever", "PZ Cussons", "Honeywell",
    "Power Oil", "Mamador",
)
_DRINK_BRANDS = ("Coca-Cola", "Pepsi", "Nigerian Breweries", "Guinness", "Eva Water")

STARTER_BRANDS: dict[str, tuple[str, ...]] = {
    "retail": _FMCG_BRANDS,
    "supermarket": _FMCG_BRANDS,
    "wholesale_distribution": _FMCG_BRANDS,
    "general_trading": _FMCG_BRANDS,
    "pharmacy": ("Emzor", "Fidson", "May & Baker", "Swiss Pharma", "Juhel", "GSK", "Pfizer", "Sanofi"),
    "restaurant_food": _DRINK_BRANDS,
    "hospitality": _DRINK_BRANDS,
    # fashion, manufacturing, school, services: brands are too business-specific to guess.
}


@dataclass(frozen=True, slots=True)
class CatalogSeedResult:
    categories: int = 0
    brands: int = 0


def _names_for(table: dict[str, tuple[str, ...]], codes: Iterable[str]) -> list[str]:
    """Names for the given business types, in order, without duplicates (case-insensitive)."""
    seen: dict[str, str] = {}
    for code in codes:
        for name in table.get(code, ()):
            seen.setdefault(name.casefold(), name)
    return list(seen.values())


def _add_missing(model, organization, names: list[str]) -> int:
    if not names:
        return 0
    existing = {
        name.casefold()
        for name in model.objects.filter(organization=organization).values_list("name", flat=True)
    }
    rows = [model(organization=organization, name=name) for name in names if name.casefold() not in existing]
    # ignore_conflicts: a concurrent run that created the same name is not an error.
    model.objects.bulk_create(rows, ignore_conflicts=True)
    return len(rows)


def seed_starter_catalog(organization, business_type_codes: Iterable[str]) -> CatalogSeedResult:
    """
    Add the starter categories and brands of these business types that the
    organization lacks. Low level: no record is kept, so prefer
    `apply_starter_catalog`, which runs this once per business type.
    """
    from core.applications.products.models import Brand
    from core.applications.products.models import Category

    codes = list(business_type_codes)
    return CatalogSeedResult(
        categories=_add_missing(Category, organization, _names_for(STARTER_CATEGORIES, codes)),
        brands=_add_missing(Brand, organization, _names_for(STARTER_BRANDS, codes)),
    )


def apply_starter_catalog(organization, business_types: Iterable) -> CatalogSeedResult:
    """
    Give the organization the starter set of each business type it has not
    received yet, and record it. Safe to call any number of times and from
    concurrent processes: the unique record makes each pair apply exactly once
    (a racing second caller hits the constraint and skips).
    """
    from core.applications.products.models import StarterCatalogRecord

    pending = list(business_types)
    if not pending:
        return CatalogSeedResult()
    done = set(
        StarterCatalogRecord.objects.filter(organization=organization, business_type__in=pending)
        .values_list("business_type_id", flat=True)
    )
    categories = brands = 0
    for business_type in pending:
        if business_type.pk in done:
            continue
        try:
            with transaction.atomic():
                record = StarterCatalogRecord.objects.create(organization=organization, business_type=business_type)
                result = seed_starter_catalog(organization, [business_type.code])
                record.categories_added, record.brands_added = result.categories, result.brands
                record.save(update_fields=["categories_added", "brands_added", "updated_at"])
        except IntegrityError:
            continue  # another process applied it first
        done.add(business_type.pk)
        categories += result.categories
        brands += result.brands
    return CatalogSeedResult(categories=categories, brands=brands)


@dataclass(frozen=True, slots=True)
class BackfillResult:
    applied: int = 0  # organization/business type pairs that received their starter set
    failed: int = 0


def backfill_starter_catalogs(*, organization_ids: Iterable | None = None) -> BackfillResult:
    """
    Apply the starter catalog to every active organization/business type pair
    that has no record yet (or only for `organization_ids`). Only pending pairs
    are loaded, so once everything is applied this is a single indexed query.
    One organization failing is logged and skipped; it never stops the others.
    """
    from django.db.models import Exists
    from django.db.models import OuterRef

    from core.applications.products.models import StarterCatalogRecord
    from core.applications.users.models import OrganizationBusinessType

    links = (
        OrganizationBusinessType.objects.filter(organization__is_active=True)
        .annotate(
            applied=Exists(
                StarterCatalogRecord.objects.filter(
                    organization=OuterRef("organization"), business_type=OuterRef("business_type")
                )
            )
        )
        .filter(applied=False)
        .select_related("organization", "business_type")
        .order_by("organization_id", "-is_primary")
    )
    if organization_ids is not None:
        links = links.filter(organization_id__in=list(organization_ids))

    by_organization: dict = {}
    for link in links.iterator(chunk_size=500):
        by_organization.setdefault(link.organization_id, (link.organization, []))[1].append(link.business_type)

    applied = failed = 0
    for organization, business_types in by_organization.values():
        try:
            with transaction.atomic():
                apply_starter_catalog(organization, business_types)
            applied += len(business_types)
        except Exception:  # noqa: BLE001 - next organization
            failed += 1
            logger.exception("starter_catalog.backfill_failed", extra={"organization_id": str(organization.pk)})
    return BackfillResult(applied=applied, failed=failed)
