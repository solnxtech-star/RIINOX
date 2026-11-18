from core.applications.products.catalog import backfill_starter_catalogs
from core.seeding.registry import SeedResult
from core.seeding.registry import register


@register("product_catalog", depends_on=("business_types",))
def seed_product_catalog() -> SeedResult:
    """
    Starter categories/brands for every organization/business type pair not yet given them.

    Runs after every `migrate` (no data migration needed), so organizations that
    predate the feature, or a restored database, are filled in automatically.
    Each pair is applied once (StarterCatalogRecord): later runs skip it, so an
    organization's own edits and deletions are never undone.
    """
    result = backfill_starter_catalogs()
    return SeedResult(created=result.applied)
