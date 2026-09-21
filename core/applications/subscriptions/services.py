from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone

from core.applications.subscriptions.models import Plan
from core.applications.subscriptions.models import PlanFeature
from core.applications.subscriptions.models import Subscription
from core.helper.enums import SubscriptionStatus

if TYPE_CHECKING:
    from core.applications.users.models import Organization

DEFAULT_PLAN_NAME = "Free"

# Statuses in which paid capabilities are honoured. PAST_DUE / CANCELED /
# EXPIRED lose them (add a grace period here if the business wants one).
GOOD_STANDING = (SubscriptionStatus.ACTIVE, SubscriptionStatus.TRIALING)


def get_default_plan() -> Plan:
    plan = Plan.objects.filter(name__iexact=DEFAULT_PLAN_NAME, is_active=True).first()
    if plan is None:
        # Server misconfiguration, not a client error -> surfaces as a 500.
        raise ImproperlyConfigured(f"Default plan '{DEFAULT_PLAN_NAME}' is not configured.")
    return plan


def start_subscription(organization: Organization, plan: Plan | None = None) -> Subscription:
    """Start an organization's subscription (default plan unless given)."""
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


def plan_has_feature(organization: Organization, feature_code: str) -> bool:
    """
    True if the plan enables `feature_code` AND the subscription is in good
    standing (PRD §10: enforced server-side). Load organizations with
    `.with_plan()` to avoid one extra query per call.
    """
    subscription = getattr(organization, "subscription", None)
    if subscription is None or subscription.status not in GOOD_STANDING:
        return False
    return PlanFeature.objects.filter(
        plan_id=subscription.plan_id,
        feature__code=feature_code,
        enabled=True,
    ).exists()


def get_plan_limit(organization: Organization, limit_field: str) -> int | None:
    """
    Numeric plan limit (e.g. "max_users"), or None when unlimited. Project
    convention: null or 0 means unlimited. Enforced server-side (PRD §10).
    """
    plan = organization.current_plan
    limit = getattr(plan, limit_field, None) if plan else None
    return limit or None
