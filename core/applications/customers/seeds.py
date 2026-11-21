from core.applications.customers.walk_in import ensure_walk_in_customers
from core.seeding.registry import SeedResult
from core.seeding.registry import register


@register("walk_in_customers")
def seed_walk_in_customers() -> SeedResult:
    """
    Every organization's built-in Walk-in Customer. Runs after every `migrate`, so
    organizations created before the feature (or a restored database) get theirs
    without a data migration. Idempotent: organizations that have one are skipped.
    """
    return SeedResult(created=ensure_walk_in_customers())
