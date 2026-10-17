from __future__ import annotations

from dataclasses import dataclass
from dataclasses import fields
from typing import Any
from typing import Final

from django.core.exceptions import ImproperlyConfigured

from core.helper.enums import FeatureCode

DEFAULT_PLAN_CODE: Final = "free"


@dataclass(frozen=True, slots=True)
class FeatureSpec:
    code: FeatureCode
    name: str
    description: str


@dataclass(frozen=True, slots=True)
class PlanSpec:
    """
    Seed definition of a plan. Every limit is a required argument so that
    "unlimited" (None) is always written down on purpose, never by omission.
    """

    code: str
    name: str
    description: str
    is_free: bool
    is_default: bool
    max_users: int | None
    max_products: int | None
    max_locations: int | None
    max_transactions_per_month: int | None
    storage_limit_mb: int | None
    trial_period_days: int = 0
    is_public: bool = True
    is_active: bool = True
    sort_order: int = 0
    features: frozenset[FeatureCode] = frozenset()

    def model_fields(self) -> dict[str, Any]:
        """Kwargs for Plan(...): every field except the feature set."""
        return {f.name: getattr(self, f.name) for f in fields(self) if f.name != "features"}


# Code-owned catalog: synced on every seed run, never deleted.
FEATURES: Final = (
    FeatureSpec(
        FeatureCode.API_ACCESS, "API access",
        "Programmatic access to the organization's data."
    ),
    FeatureSpec(
        FeatureCode.CUSTOM_TEMPLATES, "Custom document templates",
        "Branded invoice and receipt templates."
    ),
    FeatureSpec(
        FeatureCode.INTEGRATIONS, "Integrations", "Connect third-party services."
    ),
    FeatureSpec(
        FeatureCode.SMS_NOTIFICATIONS,
        "SMS notifications",
        "Send alerts and reminders by SMS."
    ),
    FeatureSpec(
        FeatureCode.PRIORITY_SUPPORT, "Priority support",
        "Faster responses from support."
    ),
)

# Business-owned: created once, never overwritten. Paid plans and their PlanPrice
# rows are created in the admin, not seeded (pricing is a business decision).
DEFAULT_PLAN: Final = PlanSpec(
    code=DEFAULT_PLAN_CODE,
    name="Free",
    description="Core business management for small teams getting started.",
    is_free=True,
    is_default=True,
    trial_period_days=0,  # 0 -> subscription starts ACTIVE, not TRIALING
    max_users=3,
    max_products=100,
    max_locations=1,
    max_transactions_per_month=200,
    storage_limit_mb=500,
    features=frozenset(),  # paid plans unlock features (PRD §10)
)


def _validate_catalog() -> None:
    """Fail at import, not at 2am: every FeatureCode must have exactly one catalog entry."""
    codes = [spec.code for spec in FEATURES]
    if len(codes) != len(set(codes)) or set(codes) != set(FeatureCode):
        msg = "FEATURES must contain exactly one entry per FeatureCode member."
        raise ImproperlyConfigured(msg)


_validate_catalog()
