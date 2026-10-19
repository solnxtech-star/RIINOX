from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from django.db import transaction
from django.utils import timezone

from core.applications.subscriptions.models import BillingPaymentMethod
from core.applications.subscriptions.models import PaymentTransaction
from core.applications.subscriptions.providers import PaymentEvent
from core.applications.subscriptions.providers import PaymentOutcome
from core.applications.subscriptions.providers import SavedMethod
from core.applications.subscriptions.services import lifecycle
from core.applications.subscriptions.services.periods import add_interval
from core.helper.enums import BillingPaymentMethodStatus
from core.helper.enums import PaymentTransactionKind
from core.helper.enums import PaymentTransactionStatus as Status

logger = logging.getLogger("billing")

# A customer who finishes checkout after we gave up on it (ABANDONED) still gets what they paid for.
_SETTLEABLE_TO_SUCCESS = (Status.PENDING, Status.ABANDONED)


class SettlementResult(StrEnum):
    APPLIED = "applied"
    DUPLICATE = "duplicate"  # already settled by an earlier delivery: a safe no-op
    UNKNOWN_REFERENCE = "unknown_reference"
    AMOUNT_MISMATCH = "amount_mismatch"
    PENDING = "pending"  # the provider has no final answer yet


@dataclass(frozen=True, slots=True)
class Settlement:
    result: SettlementResult
    payment: PaymentTransaction | None = None


def _lock_subscription_first(payment: PaymentTransaction) -> None:
    """
    LOCK ORDER (global, never invert): webhook event -> subscription -> payment.
    Renewal preparation locks the subscription and then touches the payment row, so settlement
    must take the subscription lock BEFORE it flips the payment row. The reverse order deadlocks
    under concurrent deliveries (found by tests/test_concurrency.py on PostgreSQL).
    """
    lifecycle.lock_subscription(organization_id=payment.organization_id)


def apply_payment_event(event: PaymentEvent, *, now: datetime | None = None) -> Settlement:
    now = now or timezone.now()
    payment = PaymentTransaction.objects.with_detail().filter(reference=event.reference).first()
    if payment is None:
        logger.warning("billing.unknown_reference reference=%s", event.reference)
        return Settlement(SettlementResult.UNKNOWN_REFERENCE)

    if event.outcome is PaymentOutcome.PENDING:
        return Settlement(SettlementResult.PENDING, payment)
    if event.outcome is PaymentOutcome.SUCCESS:
        return _settle_success(payment, event, now)
    return _settle_failure(payment, event, now)


def _settle_success(payment: PaymentTransaction, event: PaymentEvent, now: datetime) -> Settlement:
    # Trust nothing about a signed event except that it came from the provider: the amount
    # and currency must match what we asked for, or nobody gets a plan for the wrong money.
    if event.amount_minor != payment.amount_minor or event.currency != payment.currency:
        return _reject_mismatch(payment, event, now)

    with transaction.atomic():
        _lock_subscription_first(payment)
        updates: dict[str, object] = {
            "status": Status.SUCCESS,
            "paid_at": event.paid_at or now,
            "raw_webhook_payload": event.redacted_payload,
            "failure_code": "",
            "failure_message": "",
            "updated_at": now,
        }
        period_start = period_end = None
        if payment.kind == PaymentTransactionKind.CHECKOUT:
            period_start, period_end = now, add_interval(now, payment.plan_price.interval)
            updates.update(period_start=period_start, period_end=period_end)

        # The conditional UPDATE is the concurrency control: of any number of racing
        # deliveries, exactly one flips the row and proceeds past this line.
        flipped = PaymentTransaction.objects.filter(pk=payment.pk, status__in=_SETTLEABLE_TO_SUCCESS).update(**updates)
        if not flipped:
            return Settlement(SettlementResult.DUPLICATE, payment)
        for field, value in updates.items():
            setattr(payment, field, value)

        method = _upsert_method(payment, event.saved_method) if event.saved_method else None
        if payment.kind == PaymentTransactionKind.CHECKOUT:
            lifecycle.activate_paid_plan(payment, method=method, period_start=period_start, period_end=period_end)
        else:
            lifecycle.apply_renewal(payment, now=now)
    return Settlement(SettlementResult.APPLIED, payment)


def _settle_failure(payment: PaymentTransaction, event: PaymentEvent, now: datetime) -> Settlement:
    is_renewal = payment.kind == PaymentTransactionKind.RENEWAL
    target = Status.ABANDONED if event.outcome is PaymentOutcome.ABANDONED and not is_renewal else Status.FAILED
    with transaction.atomic():
        _lock_subscription_first(payment)
        flipped = PaymentTransaction.objects.filter(pk=payment.pk, status=Status.PENDING).update(
            status=target,
            failure_code=event.failure_code[:100],
            failure_message=event.failure_message,
            raw_webhook_payload=event.redacted_payload,
            updated_at=now,
        )
        if not flipped:
            return Settlement(SettlementResult.DUPLICATE, payment)
        payment.status, payment.failure_code, payment.failure_message = target, event.failure_code[:100], event.failure_message
        if is_renewal:
            lifecycle.record_renewal_failure(payment, now=now)
    return Settlement(SettlementResult.APPLIED, payment)


def _reject_mismatch(payment: PaymentTransaction, event: PaymentEvent, now: datetime) -> Settlement:
    detail = (
        f"expected {payment.amount_minor} {payment.currency}, "
        f"provider reported {event.amount_minor} {event.currency}"
    )
    logger.error("billing.amount_mismatch reference=%s %s", payment.reference, detail)
    with transaction.atomic():
        _lock_subscription_first(payment)
        flipped = PaymentTransaction.objects.filter(pk=payment.pk, status__in=_SETTLEABLE_TO_SUCCESS).update(
            status=Status.FAILED, failure_code="amount_mismatch", failure_message=detail,
            raw_webhook_payload=event.redacted_payload, updated_at=now,
        )
        if flipped:
            payment.status, payment.failure_code, payment.failure_message = Status.FAILED, "amount_mismatch", detail
            if payment.kind == PaymentTransactionKind.RENEWAL:
                lifecycle.record_renewal_failure(payment, now=now)
            lifecycle.flag_anomaly("amount_mismatch", payment.reference, detail)
    return Settlement(SettlementResult.AMOUNT_MISMATCH, payment)


def _upsert_method(payment: PaymentTransaction, saved: SavedMethod) -> BillingPaymentMethod:
    """Save (or refresh) the reusable method. The same card re-authorized updates its row instead of duplicating."""
    values = {
        "provider_customer_id": saved.customer_id,
        "customer_email": saved.customer_email,
        "token": saved.token,
        "brand": saved.brand,
        "last4": saved.last4,
        "exp_month": saved.exp_month,
        "exp_year": saved.exp_year,
        "status": BillingPaymentMethodStatus.ACTIVE,
    }
    if not saved.fingerprint:
        return BillingPaymentMethod.objects.create(
            organization_id=payment.organization_id, provider=saved.provider, created_by=payment.initiated_by, **values,
        )
    method, _ = BillingPaymentMethod.objects.update_or_create(
        organization_id=payment.organization_id, provider=saved.provider, fingerprint=saved.fingerprint,
        defaults=values, create_defaults={**values, "created_by": payment.initiated_by},
    )
    return method
