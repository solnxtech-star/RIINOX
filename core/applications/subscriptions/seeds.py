from django.core.exceptions import ImproperlyConfigured
from django.db import IntegrityError
from django.db import transaction
from django.db.models import Q

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
    """Feature catalog (code-owned: synced on every run, never deleted)."""
    return tally(
        upsert(
            Feature,
            lookup={"code": spec.code.value},
            defaults={"name": spec.name, "description": spec.description},
        )
        for spec in FEATURES
    )


@register("default_plan", depends_on=("features",))
def seed_default_plan() -> SeedResult:
    """
    Default (Free) plan: business-owned, created once and never overwritten.

    Every new organization is subscribed to the default plan, so after this
    runs an ACTIVE plan marked is_default MUST exist; the seed fails loudly
    otherwise rather than letting signups fail later.
    """
    spec = DEFAULT_PLAN

    # Already present? Match by stable code, by name (iexact, so an admin renaming
    # "Free" -> "free" can't cause a twin), or by any plan holding the default flag
    # (an admin may have designated a different default; single_default_plan would
    # reject a second one).
    if Plan.objects.filter(
        Q(code=spec.code) | Q(name__iexact=spec.name) | Q(is_default=True),
    ).exists():
        _ensure_usable_default()
        return SeedResult(unchanged=1)

    wanted = {code.value for code in spec.features}
    features = list(Feature.objects.filter(code__in=wanted))
    missing = wanted - {feature.code for feature in features}
    if missing:
        msg = f"DEFAULT_PLAN enables unknown feature code(s): {sorted(missing)}"
        raise ImproperlyConfigured(msg)

    try:
        with transaction.atomic():  # plan and feature rows land together or not at all
            plan = Plan.objects.create(**spec.model_fields())
            PlanFeature.objects.bulk_create(
                [PlanFeature(plan=plan, feature=feature, enabled=True) for feature in features],
            )
    except IntegrityError:
        # Another process seeded between our check and our insert (e.g. several
        # containers starting at once). Only treat it as benign if the plan is
        # really there; otherwise it's a genuine data error, so re-raise.
        if not Plan.objects.filter(code=spec.code).exists():
            raise
        _ensure_usable_default()
        return SeedResult(unchanged=1)

    return SeedResult(created=1)


def _ensure_usable_default() -> None:
    """Fail the deploy if signups would have no plan to start on."""
    if not Plan.objects.filter(is_default=True, is_active=True).exists():
        msg = (
            "No active plan is marked is_default. New organizations cannot start a "
            "subscription. Mark an active plan as the default in the admin."
        )
        raise ImproperlyConfigured(
            msg,
        )
