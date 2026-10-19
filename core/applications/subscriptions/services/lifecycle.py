"""
Subscription state transitions. Each function locks the subscription row, re-checks
that the transition still applies (jobs and requests can race), changes state, and
records one audit event, all in one transaction. None of them calls a payment provider.
"""

from __future__ import annotations

import logging
from datetime import datetime
from datetime import timedelta
from typing import TYPE_CHECKING
from typing import Any

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.exceptions import ObjectDoesNotExist
from django.db import IntegrityError
from django.db import transaction
from django.utils import timezone

from core.applications.subscriptions.errors import SubscriptionErrorCode as Err
from core.applications.subscriptions.errors import fail
from core.applications.subscriptions.models import BillingPaymentMethod
from core.applications.subscriptions.models import PaymentTransaction
from core.applications.subscriptions.models import Plan
from core.applications.subscriptions.models import Subscription
from core.applications.subscriptions.queryset import billing_grace_period
from core.applications.subscriptions.services.audit import SYSTEM
from core.applications.subscriptions.services.audit import AuditContext
from core.applications.subscriptions.services.audit import record_event
from core.applications.subscriptions.services.entitlements import effective_status
from core.applications.subscriptions.services.periods import add_interval
from core.applications.subscriptions.services.usage import lifetime_usage
from core.helper.enums import BillingPaymentMethodStatus
from core.helper.enums import SubscriptionEventType as Event
from core.helper.enums import SubscriptionStatus
from core.helper.enums import UsageMetric

if TYPE_CHECKING:
    from core.applications.users.models import Organization

logger = logging.getLogger("billing")

DEFAULT_DUNNING_DAYS = (1, 3, 5)  # retry offsets from the first failure; grace period ends the process

LAPSED_STATUSES = frozenset({SubscriptionStatus.CANCELED, SubscriptionStatus.EXPIRED})
LIFETIME_METRIC_FIELDS = {
    UsageMetric.USERS: "max_users",
    UsageMetric.PRODUCTS: "max_products",
    UsageMetric.LOCATIONS: "max_locations",
    UsageMetric.STORAGE_MB: "storage_limit_mb",
}


def dunning_schedule() -> tuple[timedelta, ...]:
    days = getattr(settings, "BILLING_DUNNING_DAYS", DEFAULT_DUNNING_DAYS)
    return tuple(timedelta(days=d) for d in days)


# --------------------------------------------------------------------------- #
# Reads and locking
# --------------------------------------------------------------------------- #
def get_default_plan() -> Plan:
    plan = Plan.objects.filter(is_default=True, is_active=True).first()
    if plan is None:
        msg = "No active plan is marked is_default."
        raise ImproperlyConfigured(msg)  # server misconfiguration -> 500
    return plan


def get_subscription(organization: Organization) -> Subscription | None:
    """
    Convenience read through the reverse relation, which Django caches on the instance:
    fine for display, but it can be stale after a change in the same process. Anything
    that decides money or access must query (or lock) the row instead.
    """
    try:
        return organization.subscription
    except ObjectDoesNotExist:
        return None


def lock_subscription(
    *, organization_id: Any = None, pk: Any = None
) -> Subscription | None:
    """
    Row-locked subscription with plan, price and payment method loaded. Use inside atomic().
    If both organization_id and pk are given, pk takes precedence. Returns None if
    the subscription does not exist.
    """
    queryset = Subscription.objects.with_billing().locked()
    if pk is not None:
        return queryset.filter(pk=pk).first()
    return queryset.filter(organization_id=organization_id).first()


def _require_locked(organization: Organization) -> Subscription:
    subscription = lock_subscription(organization_id=organization.pk)
    if subscription is None:
        raise fail(Err.SUBSCRIPTION_REQUIRED)
    return subscription


# --------------------------------------------------------------------------- #
# Creation
# --------------------------------------------------------------------------- #
def start_subscription(
    organization: Organization,
    plan: Plan | None = None,
    *, context: AuditContext = SYSTEM,
) -> Subscription:
    """
    Start an organization's subscription on the default plan (or `plan`).
    Call at organization creation.
    """
    plan = plan or get_default_plan()
    now = timezone.now()
    if plan.trial_period_days and not plan.is_free:
        status = SubscriptionStatus.TRIALING
        trial_ends_at = now + timedelta(days=plan.trial_period_days)
        period_start, period_end = now, trial_ends_at
    else:
        status = SubscriptionStatus.ACTIVE
        trial_ends_at = period_start = period_end = None  # free plans never lapse

    try:
        with transaction.atomic():
            subscription = Subscription.objects.create(
                organization=organization,
                plan=plan, status=status, trial_ends_at=trial_ends_at,
                current_period_start=period_start, current_period_end=period_end,
            )
            record_event(subscription, Event.CREATED, to_plan=plan, context=context)
    except IntegrityError:
        if Subscription.objects.filter(organization=organization).exists():
            raise fail(Err.SUBSCRIPTION_ALREADY_EXISTS) from None
        raise
    return subscription


# --------------------------------------------------------------------------- #
# Payment-driven transitions (called by payments.apply_payment_event, inside its transaction)
# --------------------------------------------------------------------------- #
def activate_paid_plan(
    payment: PaymentTransaction,
    *,
    method: BillingPaymentMethod | None,
    period_start: datetime,
    period_end: datetime,
    context: AuditContext = SYSTEM,
) -> Subscription:
    """
    A checkout payment succeeded: put the organization on the purchased plan,
    with a fresh period.
    """
    price = payment.plan_price
    if price is None:
        msg = "A checkout payment must carry a plan_price."
        raise ValueError(msg)

    subscription = lock_subscription(organization_id=payment.organization_id)
    if subscription is None:
        # Money was taken for an organization with no subscription row:
        # heal it rather than lose the payment.
        subscription = Subscription.objects.create(
            organization_id=payment.organization_id,
            plan=payment.plan, status=SubscriptionStatus.ACTIVE,
        )
        logger.warning("billing.subscription_healed organization=%s", payment.organization_id)

    from_status, from_plan = subscription.status, subscription.plan
    was_paid_and_active = (
        effective_status(subscription, now=period_start) == SubscriptionStatus.ACTIVE
        and not from_plan.is_free
        and subscription.plan_price_id is not None
    )
    changed_plan = subscription.plan_price_id != price.pk

    subscription.plan = payment.plan
    subscription.plan_price = price
    subscription.status = SubscriptionStatus.ACTIVE
    subscription.trial_ends_at = None
    subscription.current_period_start = period_start
    subscription.current_period_end = period_end
    subscription.cancel_at_period_end = False
    subscription.canceled_at = None
    subscription.ended_at = None
    subscription.renewal_attempts = 0
    subscription.next_retry_at = None
    subscription.past_due_since = None
    if method is not None:
        subscription.default_payment_method = method
    subscription.save()

    record_event(
        subscription,
        Event.PLAN_CHANGED if was_paid_and_active and changed_plan else Event.ACTIVATED,
        from_status=from_status, from_plan=from_plan, to_plan=payment.plan, payment=payment, context=context,
        metadata={
            "auto_renew": subscription.default_payment_method_id is not None,
            "credit_applied_minor": payment.credit_applied_minor,
            "amount_minor": payment.amount_minor,
        },
    )
    return subscription


def apply_renewal(payment: PaymentTransaction, *, now: datetime, context: AuditContext = SYSTEM) -> Subscription | None:
    """An automatic renewal charge succeeded: advance the period."""
    subscription = lock_subscription(organization_id=payment.organization_id)
    if subscription is None or subscription.plan_price_id != payment.plan_price_id:
        # The plan changed while this charge was in flight. Money moved but the books disagree: a human decides.
        logger.error("billing.renewal_settled_after_change reference=%s", payment.reference)
        flag_anomaly("renewal_settled_after_change", payment.reference, "Subscription changed during the charge.")
        return None

    start, end = payment.period_start, payment.period_end
    if end is None or end <= now:  # a very late success: never grant a period that is already over
        start, end = now, add_interval(now, payment.plan_price.interval)

    from_status = subscription.status
    subscription.status = SubscriptionStatus.ACTIVE
    subscription.current_period_start = start
    subscription.current_period_end = end
    subscription.renewal_attempts = 0
    subscription.next_retry_at = None
    subscription.past_due_since = None
    subscription.ended_at = None
    subscription.save()
    record_event(
        subscription, Event.RENEWED, from_status=from_status, payment=payment, context=context,
        metadata={"attempt": payment.attempt_number, "amount_minor": payment.amount_minor},
    )
    return subscription


def record_renewal_failure(payment: PaymentTransaction, *, now: datetime) -> Subscription | None:
    """An automatic renewal charge failed: enter dunning, schedule the next retry."""
    subscription = lock_subscription(organization_id=payment.organization_id)
    if subscription is None or subscription.status not in (SubscriptionStatus.ACTIVE, SubscriptionStatus.PAST_DUE):
        return None

    from_status = subscription.status
    first_failure = from_status == SubscriptionStatus.ACTIVE
    subscription.renewal_attempts += 1
    if first_failure:
        subscription.status = SubscriptionStatus.PAST_DUE
        subscription.past_due_since = now
    schedule = dunning_schedule()
    index = subscription.renewal_attempts - 1
    subscription.next_retry_at = subscription.past_due_since + schedule[index] if index < len(schedule) else None
    subscription.save()

    record_event(
        subscription, Event.PAST_DUE if first_failure else Event.RENEWAL_FAILED, from_status=from_status,
        payment=payment,
        metadata={"attempt": subscription.renewal_attempts, "failure_code": payment.failure_code},
    )
    return subscription


def flag_anomaly(kind: str, reference: str, detail: str) -> None:
    from core.applications.subscriptions.signals import billing_anomaly

    transaction.on_commit(lambda: billing_anomaly.send(sender=PaymentTransaction, kind=kind, reference=reference, detail=detail))


# --------------------------------------------------------------------------- #
# Time-driven transitions (one subscription per call; the jobs loop over these)
# --------------------------------------------------------------------------- #
def end_period(subscription_id: Any, *, now: datetime | None = None) -> Subscription | None:
    """A paid period ended and will not renew (cancelled, or no usable payment method): access ends."""
    now = now or timezone.now()
    with transaction.atomic():
        subscription = lock_subscription(pk=subscription_id)
        if (
            subscription is None
            or subscription.status != SubscriptionStatus.ACTIVE
            or subscription.plan.is_free
            or subscription.current_period_end is None
            or subscription.current_period_end > now
        ):
            return None
        method = subscription.default_payment_method
        method_usable = method is not None and method.status == BillingPaymentMethodStatus.ACTIVE
        if not subscription.cancel_at_period_end and method_usable:
            return None  # it will renew: the renewal path owns this one

        canceled = subscription.cancel_at_period_end
        subscription.status = SubscriptionStatus.CANCELED if canceled else SubscriptionStatus.EXPIRED
        subscription.ended_at = subscription.current_period_end
        subscription.save()
        record_event(
            subscription, Event.CANCELED if canceled else Event.EXPIRED, from_status=SubscriptionStatus.ACTIVE,
            metadata={"reason": "period_end_canceled" if canceled else "no_payment_method"},
        )
        return subscription


def expire_trial(subscription_id: Any, *, now: datetime | None = None) -> Subscription | None:
    now = now or timezone.now()
    with transaction.atomic():
        subscription = lock_subscription(pk=subscription_id)
        if subscription is None or subscription.status != SubscriptionStatus.TRIALING:
            return None
        if subscription.trial_ends_at is not None and subscription.trial_ends_at > now:
            return None
        subscription.status = SubscriptionStatus.EXPIRED
        subscription.ended_at = subscription.trial_ends_at or now
        subscription.save()
        record_event(subscription, Event.EXPIRED, from_status=SubscriptionStatus.TRIALING, metadata={"reason": "trial_ended"})
        return subscription


def expire_past_due(subscription_id: Any, *, now: datetime | None = None) -> Subscription | None:
    """The grace period ran out without a successful renewal: access ends."""
    now = now or timezone.now()
    with transaction.atomic():
        subscription = lock_subscription(pk=subscription_id)
        if subscription is None or subscription.status != SubscriptionStatus.PAST_DUE:
            return None
        since = subscription.past_due_since
        if since is not None and since + billing_grace_period() > now:
            return None
        subscription.status = SubscriptionStatus.EXPIRED
        subscription.ended_at = (since + billing_grace_period()) if since else now
        subscription.next_retry_at = None
        subscription.save()
        record_event(subscription, Event.EXPIRED, from_status=SubscriptionStatus.PAST_DUE, metadata={"reason": "grace_expired"})
        return subscription


# --------------------------------------------------------------------------- #
# Customer-driven transitions
# --------------------------------------------------------------------------- #
def cancel_subscription(
    *, organization: Organization, immediately: bool = False, context: AuditContext = SYSTEM,
) -> Subscription:
    """
    Graceful by default: access continues to the end of the paid period and it won't
    renew. immediately=True ends access now (fraud, refund): restrict it to staff in the API layer.
    """
    with transaction.atomic():
        subscription = _require_locked(organization)
        if subscription.plan.is_free or subscription.status in LAPSED_STATUSES:
            raise fail(Err.SUBSCRIPTION_INVALID_STATE, message="There is no active paid subscription to cancel.")

        now = timezone.now()
        from_status = subscription.status
        if immediately:
            subscription.status = SubscriptionStatus.CANCELED
            subscription.canceled_at = now
            subscription.ended_at = now
            subscription.next_retry_at = None
            subscription.save()
            record_event(subscription, Event.CANCELED, from_status=from_status, context=context, metadata={"immediate": True})
        elif not subscription.cancel_at_period_end:  # already requested: idempotent
            subscription.cancel_at_period_end = True
            subscription.canceled_at = now
            subscription.save()
            record_event(subscription, Event.CANCEL_REQUESTED, from_status=from_status, context=context)
        return subscription


def resume_subscription(*, organization: Organization, context: AuditContext = SYSTEM) -> Subscription:
    """Undo a pending cancellation before the paid period ends."""
    with transaction.atomic():
        subscription = _require_locked(organization)
        status = effective_status(subscription)
        if not subscription.cancel_at_period_end or status not in (SubscriptionStatus.ACTIVE, SubscriptionStatus.TRIALING):
            raise fail(Err.SUBSCRIPTION_INVALID_STATE, message="There is no pending cancellation to undo.")
        subscription.cancel_at_period_end = False
        subscription.canceled_at = None
        subscription.save()
        record_event(subscription, Event.CANCEL_REVOKED, from_status=subscription.status, context=context)
        return subscription


def set_default_payment_method(
    *, organization: Organization, method_id: Any, context: AuditContext = SYSTEM,
) -> Subscription:
    with transaction.atomic():
        subscription = _require_locked(organization)
        method = BillingPaymentMethod.objects.filter(
            pk=method_id, organization=organization, status=BillingPaymentMethodStatus.ACTIVE,
        ).first()
        if method is None:
            raise fail(Err.PAYMENT_METHOD_NOT_FOUND)
        subscription.default_payment_method = method
        subscription.save()
        record_event(subscription, Event.PAYMENT_METHOD_CHANGED, from_status=subscription.status, context=context)
        return subscription


def switch_to_free(*, organization: Organization, context: AuditContext = SYSTEM) -> Subscription:
    """
    A lapsed (read-only) organization may move to the default Free plan if its data
    fits Free's limits. Otherwise it stays read-only until it pays or reduces its data.
    """
    with transaction.atomic():
        subscription = _require_locked(organization)
        if effective_status(subscription) not in LAPSED_STATUSES:
            raise fail(
                Err.SUBSCRIPTION_INVALID_STATE,
                message="Only a lapsed subscription can move to the Free plan. Cancel instead to end at period end.",
            )
        free_plan = get_default_plan()
        if not free_plan.is_free:
            raise ImproperlyConfigured("The default plan must be a free plan.")

        used = lifetime_usage(organization)
        exceeded = {
            metric.value: {"used": used.get(metric.value, 0), "limit": getattr(free_plan, field)}
            for metric, field in LIFETIME_METRIC_FIELDS.items()
            if getattr(free_plan, field) is not None and used.get(metric.value, 0) > getattr(free_plan, field)
        }
        if exceeded:
            raise fail(
                Err.USAGE_EXCEEDS_PLAN,
                message="Your current usage is above the Free plan's limits. Reduce it, or renew your plan.",
                exceeded=exceeded,
            )

        from_status, from_plan = subscription.status, subscription.plan
        subscription.plan = free_plan
        subscription.plan_price = None
        subscription.status = SubscriptionStatus.ACTIVE
        subscription.trial_ends_at = subscription.current_period_start = subscription.current_period_end = None
        subscription.cancel_at_period_end = False
        subscription.canceled_at = subscription.ended_at = None
        subscription.renewal_attempts = 0
        subscription.next_retry_at = subscription.past_due_since = None
        subscription.save()
        record_event(
            subscription, Event.PLAN_CHANGED, from_status=from_status, from_plan=from_plan, to_plan=free_plan,
            context=context, metadata={"to": "free"},
        )
        return subscription
