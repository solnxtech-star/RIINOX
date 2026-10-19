from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from core.applications.subscriptions.errors import SubscriptionErrorCode as Err
from core.applications.subscriptions.errors import fail
from core.applications.subscriptions.models import PlanFeature
from core.applications.subscriptions.models import Subscription
from core.applications.subscriptions.queryset import billing_grace_period
from core.applications.subscriptions.queryset import billing_renewal_leeway
from core.helper.enums import SubscriptionStatus
from core.helper.enums import UsageMetric

logger = logging.getLogger("billing")

CACHE_VERSION = 1
DEFAULT_CACHE_TTL_SECONDS = 300

# Lapse policy B: anything outside these statuses is read-only.
FULL_ACCESS_STATUSES = frozenset(
    {SubscriptionStatus.ACTIVE, SubscriptionStatus.TRIALING, SubscriptionStatus.PAST_DUE},
)
GOOD_STANDING = tuple(FULL_ACCESS_STATUSES)


# Usage metric -> the Plan column holding its limit (NULL = unlimited, 0 = none allowed).
METRIC_LIMIT_FIELDS = {
    UsageMetric.USERS: "max_users",
    UsageMetric.PRODUCTS: "max_products",
    UsageMetric.LOCATIONS: "max_locations",
    UsageMetric.TRANSACTIONS: "max_transactions_per_month",
    UsageMetric.STORAGE_MB: "storage_limit_mb",
}


class Access(StrEnum):
    FULL = "full"
    READ_ONLY = "read_only"


_FIELD_TO_METRIC = {field: metric for metric, field in METRIC_LIMIT_FIELDS.items()}
PLAN_LIMIT_FIELDS = frozenset(_FIELD_TO_METRIC)

@dataclass(frozen=True, slots=True)
class Entitlements:
    organization_id: str
    plan_id: str
    plan_code: str
    is_free: bool
    status: str  # effective status
    access: Access
    features: frozenset[str]
    limits: dict[str, int | None]  # UsageMetric value -> limit (None = unlimited)
    period_end: datetime | None
    valid_until: datetime | None  # the snapshot is stale after this moment


# --------------------------------------------------------------------------- #
# Standing
# --------------------------------------------------------------------------- #
def effective_status(subscription: Subscription, *, now: datetime | None = None) -> str:
    """
    The stored status, corrected for time. Sweeps flip statuses on a schedule, but
    access must never depend on the scheduler being on time, so expiry is also
    derived on read. Must agree exactly with SubscriptionQuerySet.in_good_standing().
    """
    now = now or timezone.now()
    status = subscription.status

    if status == SubscriptionStatus.TRIALING:
        if subscription.trial_ends_at is None or subscription.trial_ends_at <= now:
            return SubscriptionStatus.EXPIRED
    elif status == SubscriptionStatus.ACTIVE:
        end = subscription.current_period_end
        if end is not None and not subscription.plan.is_free and end + billing_renewal_leeway() <= now:
            return SubscriptionStatus.CANCELED if subscription.cancel_at_period_end else SubscriptionStatus.EXPIRED
    elif status == SubscriptionStatus.PAST_DUE:
        since = subscription.past_due_since
        if since is None or since + billing_grace_period() <= now:
            return SubscriptionStatus.EXPIRED
    return status


def is_in_good_standing(subscription: Subscription | None) -> bool:
    return subscription is not None and effective_status(subscription) in FULL_ACCESS_STATUSES


def _valid_until(subscription: Subscription) -> datetime | None:
    status = subscription.status
    if status == SubscriptionStatus.TRIALING:
        return subscription.trial_ends_at
    if status == SubscriptionStatus.ACTIVE and not subscription.plan.is_free and subscription.current_period_end:
        return subscription.current_period_end + billing_renewal_leeway()
    if status == SubscriptionStatus.PAST_DUE and subscription.past_due_since:
        return subscription.past_due_since + billing_grace_period()
    return None


# --------------------------------------------------------------------------- #
# Snapshot cache
# --------------------------------------------------------------------------- #
def _key(organization_id: object) -> str:
    return f"billing:entitlements:v{CACHE_VERSION}:{organization_id}"


def _cache_get(organization_id: object) -> Entitlements | None:
    try:
        return cache.get(_key(organization_id))
    except Exception:  # a cache outage must degrade to the database, never to an error
        logger.warning("billing.entitlements.cache_get_failed", exc_info=True)
        return None


def _cache_set(organization_id: object, entitlements: Entitlements, now: datetime) -> None:
    ttl = getattr(settings, "BILLING_ENTITLEMENTS_TTL_SECONDS", DEFAULT_CACHE_TTL_SECONDS)
    if entitlements.valid_until is not None:
        ttl = min(ttl, int((entitlements.valid_until - now).total_seconds()))
    if ttl < 1:
        return
    try:
        cache.set(_key(organization_id), entitlements, ttl)
    except Exception:
        logger.warning("billing.entitlements.cache_set_failed", exc_info=True)


def invalidate_entitlements(organization_id: object) -> None:
    try:
        cache.delete(_key(organization_id))
    except Exception:
        logger.warning("billing.entitlements.cache_delete_failed", exc_info=True)


def invalidate_on_commit(organization_id: object) -> None:
    """Drop the snapshot only once the change is durable, so readers never re-cache stale data."""
    transaction.on_commit(lambda: invalidate_entitlements(organization_id))


def _load(organization_id: str, now: datetime) -> Entitlements | None:
    subscription = Subscription.objects.with_plan().filter(organization_id=organization_id).first()
    if subscription is None:
        return None
    plan = subscription.plan
    status = effective_status(subscription, now=now)
    features = frozenset(
        PlanFeature.objects.filter(plan_id=plan.pk, enabled=True).values_list("feature__code", flat=True),
    )
    return Entitlements(
        organization_id=organization_id,
        plan_id=str(plan.pk),
        plan_code=plan.code,
        is_free=plan.is_free,
        status=status,
        access=Access.FULL if status in FULL_ACCESS_STATUSES else Access.READ_ONLY,
        features=features,
        limits={metric.value: getattr(plan, field) for metric, field in METRIC_LIMIT_FIELDS.items()},
        period_end=subscription.current_period_end,
        valid_until=_valid_until(subscription),
    )


def get_entitlements(organization: Any) -> Entitlements | None:
    """The organization's snapshot, or None if it has no subscription. Accepts an object or an id."""
    organization_id = str(getattr(organization, "pk", organization))
    now = timezone.now()
    cached = _cache_get(organization_id)
    if cached is not None and (cached.valid_until is None or cached.valid_until > now):
        return cached
    entitlements = _load(organization_id, now)
    if entitlements is not None:
        _cache_set(organization_id, entitlements, now)
    return entitlements


# --------------------------------------------------------------------------- #
# Public checks (PRD §10: enforced server-side)
# --------------------------------------------------------------------------- #
def require_entitlements(organization: Any) -> Entitlements:
    entitlements = get_entitlements(organization)
    if entitlements is None:
        raise fail(Err.SUBSCRIPTION_REQUIRED)  # fail closed: no subscription, no verifiable limits
    return entitlements


def ensure_subscription_active(organization: Any) -> Entitlements:
    """
    The write gate. Raises unless the organization has full access. Reads and
    exports must NOT call this: a lapsed organization keeps read-only access to
    its own data.
    """
    entitlements = require_entitlements(organization)
    if entitlements.access is not Access.FULL:
        raise fail(
            Err.SUBSCRIPTION_READ_ONLY,
            message="Your subscription has ended. Your data is safe and viewable; renew to make changes.",
            status=entitlements.status,
        )
    return entitlements


def plan_has_feature(organization: Any, feature: str) -> bool:
    entitlements = get_entitlements(organization)
    if entitlements is None or entitlements.access is not Access.FULL:
        return False
    return getattr(feature, "value", feature) in entitlements.features


def require_feature(organization: Any, feature: str) -> None:
    if not plan_has_feature(organization, feature):
        raise fail(Err.FEATURE_NOT_AVAILABLE, feature=getattr(feature, "value", feature))


def get_limit(organization: Any, metric: UsageMetric) -> int | None:
    """Plan limit for a metric: None = unlimited, 0 = none allowed. Fails closed without a subscription."""
    return require_entitlements(organization).limits[UsageMetric(metric).value]

def get_plan_limit(organization: Any, limit_field: str) -> int | None:
    """
    Numeric plan limit by Plan column name (e.g. "max_users"): None means unlimited and
    0 means none allowed. (The old "0 = unlimited" shortcut is gone: a plan like
    "Free: 0 locations" must be expressible.)

    The whitelist makes a mistyped name raise instead of quietly returning None, which would
    read as "unlimited". Fails closed: with no subscription no limit can be verified, so it
    raises SUBSCRIPTION_REQUIRED. Served from the cached snapshot like every other check.
    Prefer get_limit(org, UsageMetric.X) in new code.
    """
    try:
        metric = _FIELD_TO_METRIC[limit_field]
    except KeyError:
        msg = f"Unknown plan limit: {limit_field!r}"
        raise ValueError(msg) from None
    return get_limit(organization, metric)

__all__ = [
    "FULL_ACCESS_STATUSES",
    "GOOD_STANDING",
    "METRIC_LIMIT_FIELDS",
    "Access",
    "Entitlements",
    "effective_status",
    "ensure_subscription_active",
    "get_entitlements",
    "get_limit",
    "invalidate_entitlements",
    "invalidate_on_commit",
    "is_in_good_standing",
    "plan_has_feature",
    "require_entitlements",
    "require_feature",
]
