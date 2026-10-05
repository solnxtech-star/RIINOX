from django.core.exceptions import ImproperlyConfigured

from core.applications.subscriptions.default import DEFAULT_PLAN
from core.applications.subscriptions.default import FEATURES
from core.applications.subscriptions.models import Feature
from core.applications.subscriptions.models import Plan
from core.applications.subscriptions.models import PlanFeature
from core.seeding.helper import tally
from core.seeding.helper import upsert
from core.seeding.registry import SeedResult
from core.seeding.registry import register


@register("features")
def seed_features() -> SeedResult:
    """Feature catalog (code-owned: synced, never deleted)."""
    return tally(
        upsert(
            Feature,
            lookup={"code": entry["code"]},
            defaults={"name": entry["name"], "description": entry["description"]},
        )
        for entry in FEATURES
    )


@register("default_plan", depends_on=("features",))
def seed_default_plan() -> SeedResult:
    """Default (Free) plan: business-owned, created once and never overwritten."""
    spec = dict(DEFAULT_PLAN)
    feature_codes = set(spec.pop("features"))

    # iexact matches get_default_plan(); an admin renaming "Free" -> "free" must not create a twin.
    if Plan.objects.filter(name__iexact=spec["name"]).exists():
        return SeedResult(unchanged=1)

    features = list(Feature.objects.filter(code__in=feature_codes))
    missing = feature_codes - {feature.code for feature in features}
    if missing:
        raise ImproperlyConfigured(
            f"DEFAULT_PLAN enables unknown feature code(s): {sorted(missing)}"
        )

    plan = Plan.objects.create(**spec)
    PlanFeature.objects.bulk_create(
        [PlanFeature(plan=plan, feature=f, enabled=True) for f in features]
    )
    return SeedResult(created=1)
