from __future__ import annotations

from datetime import date
from typing import Any

from django.db.models import F
from django.db.models.functions import Greatest
from django.utils import timezone

from core.applications.subscriptions.errors import SubscriptionErrorCode as Err
from core.applications.subscriptions.errors import fail
from core.applications.subscriptions.models import LIFETIME_PERIOD
from core.applications.subscriptions.models import UsageCounter
from core.applications.subscriptions.services.entitlements import (
    ensure_subscription_active,
)
from core.applications.subscriptions.services.entitlements import require_entitlements
from core.helper.enums import UsageMetric

MONTHLY_METRICS = frozenset({UsageMetric.TRANSACTIONS})


def _organization_id(organization: Any) -> str:
    return str(getattr(organization, "pk", organization))


def current_period(metric: UsageMetric) -> date:
    """First of the current UTC month for monthly metrics; a fixed sentinel for running totals."""
    if metric in MONTHLY_METRICS:
        return timezone.now().date().replace(day=1)
    return LIFETIME_PERIOD


def _counters(organization_id: str, metric: UsageMetric):
    return UsageCounter.objects.filter(
        organization_id=organization_id, metric=metric, period_start=current_period(metric),
    )


def _try_increment(organization_id: str, metric: UsageMetric, amount: int, limit: int | None) -> bool:
    queryset = _counters(organization_id, metric)
    if limit is not None:
        queryset = queryset.filter(value__lte=limit - amount)  # value + amount <= limit
    return queryset.update(value=F("value") + amount, updated_at=timezone.now()) > 0


def consume_usage(organization: Any, metric: UsageMetric, amount: int = 1) -> None:
    """
    Reserve `amount` of `metric`, or raise PLAN_LIMIT_REACHED. Also the write gate:
    raises SUBSCRIPTION_READ_ONLY for a lapsed organization. An organization already
    over a limit (after a downgrade) keeps its data but cannot add more.
    """
    if amount <= 0:
        raise ValueError("amount must be positive")
    metric = UsageMetric(metric)
    organization_id = _organization_id(organization)
    limit = ensure_subscription_active(organization_id).limits[metric.value]

    if _try_increment(organization_id, metric, amount, limit):  # the common path: one query
        return
    # Nothing updated: the counter row doesn't exist yet, or the limit is reached.
    UsageCounter.objects.get_or_create(
        organization_id=organization_id, metric=metric, period_start=current_period(metric),
    )
    if _try_increment(organization_id, metric, amount, limit):
        return

    current = _counters(organization_id, metric).values_list("value", flat=True).first() or 0
    friendly = metric.label.lower()
    raise fail(
        Err.PLAN_LIMIT_REACHED,
        message=f"Your plan allows up to {limit} {friendly}. Upgrade your plan to add more.",
        metric=metric.value,
        limit=limit,
        current=current,
    )


def release_usage(organization: Any, metric: UsageMetric, amount: int = 1) -> None:
    """Give usage back (a deleted user, product, location). Never goes below zero."""
    if amount <= 0:
        raise ValueError("amount must be positive")
    metric = UsageMetric(metric)
    _counters(_organization_id(organization), metric).update(
        value=Greatest(F("value") - amount, 0), updated_at=timezone.now(),
    )


def get_usage(organization: Any, metric: UsageMetric) -> int:
    metric = UsageMetric(metric)
    return _counters(_organization_id(organization), metric).values_list("value", flat=True).first() or 0


def set_usage(organization: Any, metric: UsageMetric, value: int) -> None:
    """Overwrite a counter with a recounted truth. For the periodic reconciliation job."""
    metric = UsageMetric(metric)
    UsageCounter.objects.update_or_create(
        organization_id=_organization_id(organization), metric=metric, period_start=current_period(metric),
        defaults={"value": max(value, 0)},
    )


def lifetime_usage(organization: Any) -> dict[str, int]:
    """Running-total counters (users, products, locations, storage) keyed by metric value."""
    rows = UsageCounter.objects.filter(organization_id=_organization_id(organization), period_start=LIFETIME_PERIOD)
    return dict(rows.values_list("metric", "value"))


def usage_summary(organization: Any) -> dict[str, dict[str, int | None]]:
    """{metric: {"used": n, "limit": n | None}} for the billing page."""
    entitlements = require_entitlements(organization)
    return {
        metric.value: {"used": get_usage(organization, metric), "limit": entitlements.limits[metric.value]}
        for metric in UsageMetric
    }
