"""
Recovering from missing news. Paystack sends no failure webhook for one-off charges and
webhooks can be lost, so pending payments are periodically verified with the provider.
"""

from __future__ import annotations

import logging
from datetime import datetime
from datetime import timedelta

from django.utils import timezone

from core.applications.subscriptions.models import PaymentTransaction
from core.applications.subscriptions.providers.base import PaymentOutcome
from core.applications.subscriptions.providers.errors import ProviderConfigurationError
from core.applications.subscriptions.providers.errors import ProviderUnavailableError
from core.applications.subscriptions.providers.errors import TransactionNotFoundError
from core.applications.subscriptions.providers.registry import get_provider
from core.applications.subscriptions.services.payments import Settlement
from core.applications.subscriptions.services.payments import apply_payment_event
from core.helper.enums import PaymentTransactionStatus as Status

logger = logging.getLogger("billing")

# Until a checkout is this old, "abandoned" from the provider only means "not paid yet".
STALE_AFTER = timedelta(hours=24)


def reconcile_payment(payment: PaymentTransaction, *, now: datetime | None = None) -> Settlement | None:
    """Ask the provider what really happened to a pending payment and settle it."""
    now = now or timezone.now()
    provider = get_provider(payment.provider)
    try:
        event = provider.verify_transaction(payment.reference)
    except TransactionNotFoundError:
        # The provider never heard of it (e.g. our initialize call died midway): nothing can ever settle it.
        _abandon(payment, "not_found", now)
        return None
    except ProviderUnavailableError:
        return None  # try again next run
    except ProviderConfigurationError:
        logger.exception("billing.reconcile_misconfigured provider=%s", payment.provider)
        raise

    if event.outcome is PaymentOutcome.ABANDONED and payment.created_at > now - STALE_AFTER:
        return None  # the customer may still be on the checkout page
    return apply_payment_event(event, now=now)


def reconcile_pending(*, limit: int = 200, now: datetime | None = None) -> int:
    """
    Verify pending payments old enough that their webhook should have arrived.
    Returns how many were checked
    """
    now = now or timezone.now()
    payments = list(PaymentTransaction.objects.reconcilable().order_by("created_at")[:limit])
    for payment in payments:
        reconcile_payment(payment, now=now)
    return len(payments)


def abandon_stale(*, limit: int = 500, now: datetime | None = None) -> int:
    """After reconciliation had its chance, mark long-pending checkouts ABANDONED. Rescuable by a late success."""
    now = now or timezone.now()
    ids = list(PaymentTransaction.objects.checkouts().stale().values_list("pk", flat=True)[:limit])
    return PaymentTransaction.objects.filter(pk__in=ids, status=Status.PENDING).update(
        status=Status.ABANDONED, failure_code="abandoned", updated_at=now,
    )


def _abandon(payment: PaymentTransaction, code: str, now: datetime) -> None:
    PaymentTransaction.objects.filter(pk=payment.pk, status=Status.PENDING).update(
        status=Status.ABANDONED, failure_code=code, updated_at=now,
    )
