from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from django.core.exceptions import ImproperlyConfigured
from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.utils import timezone

from core.applications.subscriptions.errors import SubscriptionErrorCode as Err
from core.applications.subscriptions.errors import fail
from core.applications.subscriptions.models import Plan
from core.applications.subscriptions.models import PlanFeature
from core.applications.subscriptions.models import Subscription
from core.helper.enums import SubscriptionStatus

if TYPE_CHECKING:
    from core.applications.users.models import Organization

__all__ = [
    "DEFAULT_PLAN_NAME",
    "GOOD_STANDING",
    "PLAN_LIMIT_FIELDS",
    "effective_status",
    "get_default_plan",
    "get_plan_limit",
    "get_subscription",
    "is_in_good_standing",
    "plan_has_feature",
    "require_feature",
    "start_subscription",
]

DEFAULT_PLAN_NAME = "Free"

# Statuses in which paid capabilities are honoured. PAST_DUE / CANCELED /
# EXPIRED lose them (add a grace period here if the business wants one).
GOOD_STANDING = (SubscriptionStatus.ACTIVE, SubscriptionStatus.TRIALING)

# Numeric plan limits (PRD §10). A whitelist makes a mistyped limit name fail
# loudly instead of returning None, which would read as "unlimited".
PLAN_LIMIT_FIELDS = frozenset(
    {
        "max_users",
        "max_products",
        "max_locations",
        "max_transactions_per_month",
        "storage_limit_mb",
    },
)


# --------------------------------------------------------------------------- #
# Plans and subscriptions
# --------------------------------------------------------------------------- #
def get_default_plan() -> Plan:
    plan = Plan.objects.filter(name__iexact=DEFAULT_PLAN_NAME, is_active=True).first()
    if plan is None:
        # Server misconfiguration, not a client error -> surfaces as a 500.
        raise ImproperlyConfigured(f"Default plan '{DEFAULT_PLAN_NAME}' is not configured.")
    return plan


def get_subscription(organization: Organization) -> Subscription | None:
    """
    The organization's subscription, or None. Reads the reverse one-to-one, so
    organizations loaded with `.with_plan()` cost no extra query.
    """
    try:
        return organization.subscription
    except ObjectDoesNotExist:
        return None


@transaction.atomic
def start_subscription(organization: Organization, plan: Plan | None = None) -> Subscription:
    """Start an organization's subscription (default plan unless given)."""
    if Subscription.objects.filter(organization=organization).exists():
        raise fail(Err.SUBSCRIPTION_ALREADY_EXISTS)

    plan = plan or get_default_plan()
    now = timezone.now()

    if plan.trial_period_days:
        status = SubscriptionStatus.TRIALING
        trial_ends_at = now + timedelta(days=plan.trial_period_days)
    else:
        status = SubscriptionStatus.ACTIVE
        trial_ends_at = None

    return Subscription.objects.create(
        organization=organization,
        plan=plan,
        status=status,
        trial_ends_at=trial_ends_at,
        current_period_start=now,
        current_period_end=now + timedelta(days=plan.billing_period_days),
    )


# --------------------------------------------------------------------------- #
# Standing
# --------------------------------------------------------------------------- #
def effective_status(subscription: Subscription) -> str:
    """
    The stored status, except that a trial past its end date counts as EXPIRED.
    Nothing flips that status automatically, so expiry is derived on read
    (the same approach as invitation expiry).
    """
    if (
        subscription.status == SubscriptionStatus.TRIALING
        and subscription.trial_ends_at is not None
        and subscription.trial_ends_at <= timezone.now()
    ):
        return SubscriptionStatus.EXPIRED
    return subscription.status


def is_in_good_standing(subscription: Subscription | None) -> bool:
    return subscription is not None and effective_status(subscription) in GOOD_STANDING


# --------------------------------------------------------------------------- #
# Features and limits (PRD §10: enforced server-side)
# --------------------------------------------------------------------------- #
def plan_has_feature(organization: Organization, feature_code: str) -> bool:
    """
    True if the plan enables `feature_code` AND the subscription is in good
    standing. Load organizations with `.with_plan()` to avoid an extra query.
    """
    subscription = get_subscription(organization)
    if not is_in_good_standing(subscription):
        return False
    return PlanFeature.objects.filter(
        plan_id=subscription.plan_id,
        feature__code=feature_code,
        enabled=True,
    ).exists()


def require_feature(organization: Organization, feature_code: str) -> None:
    """Raises FEATURE_NOT_AVAILABLE (403) unless the plan enables the feature."""
    if not plan_has_feature(organization, feature_code):
        raise fail(Err.FEATURE_NOT_AVAILABLE, feature=feature_code)


def get_plan_limit(organization: Organization, limit_field: str) -> int | None:
    """
    Numeric plan limit (e.g. "max_users"), or None when unlimited. Project
    convention: null or 0 means unlimited.

    Fails closed: with no subscription, no limit can be verified, so raise
    instead of returning None. Limits always come from the plan, whatever the
    standing; standing only gates paid features.
    """
    if limit_field not in PLAN_LIMIT_FIELDS:
        raise ValueError(f"Unknown plan limit: {limit_field!r}")

    subscription = get_subscription(organization)
    if subscription is None:
        raise fail(Err.SUBSCRIPTION_REQUIRED)

    return getattr(subscription.plan, limit_field) or None
