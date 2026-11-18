"""
Starter product categories and brands, chosen by business type.

Categories and brands belong to each organization, so they cannot be seeded
once for the whole platform. Instead a new organization starts with the
categories and brands of its business type(s), so its product form isn't empty
on day one. They are ordinary rows afterwards: the organization can rename,
deactivate or delete them.

When this runs:
- `create_organization` (users/services.py): the primary business type.
- `set_business_types`: only the business types newly added to the organization.
- `manage.py seed_product_catalog`: on demand, for organizations that already exist.

It only ever adds missing names (compared case-insensitively). It never renames,
reactivates or deletes anything, so running it twice changes nothing. Edit the
lists below to change what new organizations get.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

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
    """Add the starter categories and brands of these business types that the organization lacks."""
    from core.applications.products.models import Brand
    from core.applications.products.models import Category

    codes = list(business_type_codes)
    return CatalogSeedResult(
        categories=_add_missing(Category, organization, _names_for(STARTER_CATEGORIES, codes)),
        brands=_add_missing(Brand, organization, _names_for(STARTER_BRANDS, codes)),
    )
