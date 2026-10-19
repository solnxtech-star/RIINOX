"""
Automatic renewals: charge the saved payment method when a paid period is due.

Shape of one renewal: (1) lock the subscription, decide it is due, write a PENDING
renewal row with a DETERMINISTIC reference, commit; (2) call the provider with no
database transaction open; (3) settle the answer through the same path as any payment.
A crashed or concurrent run derives the same reference, hits the unique constraint, and
reconciles the existing attempt instead of charging twice.
"""

from __future__ import annotations

import logging
from datetime import datetime
from enum import StrEnum
from typing import Any

from django.db import IntegrityError
from django.db import transaction
from django.utils import timezone

from core.applications.subscriptions.models import PaymentTransaction
from core.applications.subscriptions.models import Subscription
from core.applications.subscriptions.providers import PaymentEvent
from core.applications.subscriptions.providers import PaymentOutcome
from core.applications.subscriptions.providers import ProviderConfigurationError
from core.applications.subscriptions.providers import ProviderRejectedError
from core.applications.subscriptions.providers import ProviderUnavailableError
from core.applications.subscriptions.providers import get_provider
from core.applications.subscriptions.queryset import billing_renewal_lead
from core.applications.subscriptions.services.lifecycle import lock_subscription
from core.applications.subscriptions.services.payments import apply_payment_event
from core.applications.subscriptions.services.periods import add_interval
from core.applications.subscriptions.services.periods import renewal_reference
from core.applications.subscriptions.services.reconciliation import reconcile_payment
from core.helper.enums import BillingPaymentMethodStatus
from core.helper.enums import PaymentTransactionKind
from core.helper.enums import PaymentTransactionStatus as Status
from core.helper.enums import SubscriptionStatus

logger = logging.getLogger("billing")


class RenewalOutcome(StrEnum):
    SKIPPED = "skipped"      # not due, not eligible, or another worker has it
    SUCCEEDED = "succeeded"
    FAILED = "failed"        # declined: dunning started or advanced
    PENDING = "pending"      # outcome unknown: stays PENDING, reconciliation settles it


def renew_subscription(subscription_id: Any, *, now: datetime | None = None) -> RenewalOutcome:
    now = now or timezone.now()
    prepared = _prepare(subscription_id, now)
    if prepared is None:
        return RenewalOutcome.SKIPPED
    payment, is_new = prepared
    if not is_new:  # a previous run already wrote this attempt: settle it instead of charging again
        return _settle_existing(payment, now)

    method = payment.payment_method
    provider = get_provider(method.provider)
    try:
        event = provider.charge_saved_method(
            method_token=method.token, email=method.customer_email, amount_minor=payment.amount_minor,
            currency=payment.currency, reference=payment.reference,
            metadata={
                "organization_id": str(payment.organization_id), "subscription_id": str(payment.subscription_id),
                "kind": "renewal", "attempt": payment.attempt_number,
            },
        )
    except ProviderRejectedError as exc:
        event = PaymentEvent(
            payment.reference, PaymentOutcome.FAILED, payment.amount_minor, payment.currency,
            event_type="charge_authorization", failure_code=exc.code or "rejected", failure_message=str(exc),
        )
    except ProviderUnavailableError:
        logger.warning("billing.renewal_outcome_unknown reference=%s", payment.reference)
        return RenewalOutcome.PENDING  # never mark FAILED: the charge may have gone through
    except ProviderConfigurationError:
        logger.exception("billing.provider_misconfigured provider=%s", method.provider)
        raise

    apply_payment_event(event, now=now)
    return _outcome_for(event.outcome)


def _outcome_for(outcome: PaymentOutcome) -> RenewalOutcome:
    if outcome is PaymentOutcome.SUCCESS:
        return RenewalOutcome.SUCCEEDED
    if outcome is PaymentOutcome.PENDING:
        return RenewalOutcome.PENDING
    return RenewalOutcome.FAILED


def _settle_existing(payment: PaymentTransaction, now: datetime) -> RenewalOutcome:
    if payment.status != Status.PENDING:
        return RenewalOutcome.SKIPPED  # already settled
    settlement = reconcile_payment(payment, now=now)
    if settlement is None:
        return RenewalOutcome.PENDING
    refreshed = PaymentTransaction.objects.filter(pk=payment.pk).values_list("status", flat=True).first()
    if refreshed == Status.SUCCESS:
        return RenewalOutcome.SUCCEEDED
    return RenewalOutcome.FAILED if refreshed == Status.FAILED else RenewalOutcome.PENDING


def _blocker(subscription: Subscription, now: datetime) -> str | None:
    """Why this subscription must not be charged right now, or None if it is due."""
    method = subscription.default_payment_method
    if subscription.plan_price is None or subscription.plan.is_free:
        return "free_plan"
    if subscription.cancel_at_period_end:
        return "canceling"
    if method is None or method.status != BillingPaymentMethodStatus.ACTIVE:
        return "no_usable_method"
    if subscription.status == SubscriptionStatus.ACTIVE:
        due = subscription.current_period_end is not None and subscription.current_period_end <= now + billing_renewal_lead()
        return None if due else "not_due"
    if subscription.status == SubscriptionStatus.PAST_DUE:
        due = subscription.next_retry_at is not None and subscription.next_retry_at <= now
        return None if due else "not_due"
    return "status"


def _prepare(subscription_id: Any, now: datetime) -> tuple[PaymentTransaction, bool] | None:
    with transaction.atomic():
        subscription = lock_subscription(pk=subscription_id)
        if subscription is None or _blocker(subscription, now) is not None:
            return None

        method, price = subscription.default_payment_method, subscription.plan_price
        attempt = subscription.renewal_attempts + 1
        period_start = subscription.current_period_end
        period_end = add_interval(period_start, price.interval)
        reference = renewal_reference(subscription.pk, period_end, attempt)
        # Look before inserting: a plain read never waits, whereas inserting a duplicate reference
        # waits on the unique index while another transaction is updating that row.
        existing = PaymentTransaction.objects.with_detail().filter(reference=reference).first()
        if existing is not None:
            return existing, False
        try:
            with transaction.atomic():  # savepoint: a duplicate must not poison the outer transaction
                payment = PaymentTransaction.objects.create(
                    organization_id=subscription.organization_id, subscription=subscription, plan=subscription.plan,
                    plan_price=price, payment_method=method, kind=PaymentTransactionKind.RENEWAL,
                    provider=method.provider, reference=reference, amount_minor=price.amount_minor,
                    currency=price.currency, period_start=period_start, period_end=period_end, attempt_number=attempt,
                )
        except IntegrityError:
            existing = PaymentTransaction.objects.with_detail().filter(reference=reference).first()
            return (existing, False) if existing is not None else None
        payment.payment_method = method  # already loaded: no extra query after the transaction ends
        return payment, True
