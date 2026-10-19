"""
Checkout: turns "organization X wants plan Y" into a PENDING payment row plus a hosted
checkout URL. The subscription changes only when the payment later settles (payments.py).

Order matters: the PENDING row is committed BEFORE the provider is called, so a webhook
can never arrive for a reference we don't know. Do not run billing views inside
ATOMIC_REQUESTS (use @transaction.non_atomic_requests), or the row stays uncommitted
while the provider call is in flight.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from django.conf import settings
from django.db import IntegrityError
from django.db import transaction
from django.utils import timezone

from core.applications.subscriptions.errors import SubscriptionErrorCode as Err
from core.applications.subscriptions.errors import fail
from core.applications.subscriptions.models import PaymentTransaction
from core.applications.subscriptions.models import Plan
from core.applications.subscriptions.models import PlanPrice
from core.applications.subscriptions.models import Subscription
from core.applications.subscriptions.providers.base import CheckoutSession
from core.applications.subscriptions.providers.errors import NoProviderForCurrencyError
from core.applications.subscriptions.providers.errors import ProviderConfigurationError
from core.applications.subscriptions.providers.errors import ProviderRejectedError
from core.applications.subscriptions.providers.errors import ProviderUnavailableError
from core.applications.subscriptions.providers.registry import provider_for_currency
from core.applications.subscriptions.services import effective_status
from core.applications.subscriptions.services.periods import checkout_reference
from core.applications.subscriptions.services.periods import prorated_credit_minor
from core.helper.enums import BillingInterval
from core.helper.enums import PaymentTransactionKind
from core.helper.enums import PaymentTransactionStatus as Status
from core.helper.enums import SubscriptionStatus

if TYPE_CHECKING:
    from core.applications.users.models import Organization
    from core.applications.users.models import User

logger = logging.getLogger("billing")
MAX_IDEMPOTENCY_KEY_LENGTH = 100


def initiate_plan_checkout(
    *,
    organization: Organization,
    plan: Plan,
    interval: str,
    user: User,
    callback_url: str,
    idempotency_key: str = "",
    now: datetime | None = None,
) -> CheckoutSession:
    """
    Start a hosted checkout for `organization` to buy `plan` on `interval`. Card details
    never touch this API. Revalidates everything even though a serializer already did:
    a service is a boundary callable from anywhere. Authorization (Owner/Admin, PRD §55)
    belongs to the API layer; `user` is recorded as the initiator.

    A repeated `idempotency_key` returns the original attempt instead of creating another.
    """
    now = now or timezone.now()
    if len(idempotency_key) > MAX_IDEMPOTENCY_KEY_LENGTH:
        msg = "idempotency_key is too long"
        raise ValueError(msg)
    _validate_callback_url(callback_url)

    if not plan.is_active or not plan.is_public:
        raise fail(Err.PLAN_NOT_AVAILABLE, plan=plan.name)
    if plan.is_free:
        raise fail(Err.PLAN_IS_FREE, plan=plan.name)

    currency = organization.currency.upper()
    try:
        provider = provider_for_currency(currency)
    except NoProviderForCurrencyError:
        raise fail(Err.UNSUPPORTED_CURRENCY, currency=currency) from None

    price = _resolve_price(plan, currency, interval)
    # Fresh read, never organization.subscription:
    # that reverse relation is cached on the instance
    # and can be stale, and this decision moves money.
    subscription = Subscription.objects.with_billing().filter(
        organization=organization,
    ).first()
    credit = _plan_change_credit(subscription, price, now)
    amount = price.amount_minor - credit
    if amount <= 0:
        raise fail(
            Err.PLAN_CHANGE_NOT_ALLOWED,
            message=(
                "Your unused balance on the current plan covers this one, so it cannot be bought directly. "
                "Cancel the current plan at period end, then subscribe."
            ),
        )

    if idempotency_key:
        existing = PaymentTransaction.objects.filter(
            organization=organization, idempotency_key=idempotency_key
        ).first()
        if existing is not None:
            return _replay(existing, plan, price)

    reference = checkout_reference()
    try:
        with transaction.atomic():
            payment = PaymentTransaction.objects.create(
                organization=organization, subscription=subscription,
                plan=plan, plan_price=price, initiated_by=user,
                kind=PaymentTransactionKind.CHECKOUT, provider=provider.name,
                reference=reference, idempotency_key=idempotency_key, amount_minor=amount,
                credit_applied_minor=credit, currency=currency,
            )
    except IntegrityError:
        # Two identical requests raced: the other one won, so replay it.
        existing = (
            PaymentTransaction.objects.filter(
                organization=organization, idempotency_key=idempotency_key
            ).first()
            if idempotency_key else None
        )
        if existing is None:
            raise
        return _replay(existing, plan, price)

    try:
        session = provider.initialize_checkout(
            email=user.email, amount_minor=amount, currency=currency, reference=reference,
            callback_url=callback_url, save_payment_method=True,
            metadata={
                "organization_id": str(organization.pk), "plan_id": str(plan.pk),
                "plan_price_id": str(price.pk), "kind": "checkout",
            },
        )
    except ProviderRejectedError as exc:
        _mark_failed(payment, exc.code or "rejected", str(exc))
        raise fail(
            Err.CHECKOUT_FAILED,
            message="We couldn't start your payment. Please try again.",
            reference=reference
        ) from exc
    except ProviderUnavailableError as exc:
        # Safe to fail: without a checkout URL the customer cannot pay,
        # so no money can move on this reference.
        _mark_failed(payment, "provider_unavailable", str(exc))
        raise fail(
            Err.CHECKOUT_FAILED, message="Our payment provider is unavailable. Please try again shortly.",
            reference=reference, retryable=True,
        ) from exc
    except ProviderConfigurationError as exc:
        _mark_failed(payment, "configuration", str(exc))
        logger.exception("billing.provider_misconfigured provider=%s", provider.name)
        raise

    PaymentTransaction.objects.filter(pk=payment.pk).update(
        checkout_url=session.authorization_url, updated_at=timezone.now()
    )
    return session


# --------------------------------------------------------------------------- #
# Plan change pricing
# --------------------------------------------------------------------------- #
def compute_plan_change_credit(
    subscription: Subscription | None, price: PlanPrice, *, now: datetime
    ) -> int:
    """
    Unused value of the current PAID period, credited against the new charge. Credit is
    never refunded: if it covers the whole new price, checkout refuses. To drop credit
    entirely (full price on every change), make this return 0.
    """
    if subscription is None or subscription.plan.is_free or subscription.plan_price is None:
        return 0
    if effective_status(subscription, now=now) != SubscriptionStatus.ACTIVE:
        return 0  # trialing, past due and lapsed organizations pay the full price
    old = subscription.plan_price
    if old.currency != price.currency or subscription.current_period_start is None or subscription.current_period_end is None:
        return 0
    return prorated_credit_minor(
        amount_minor=old.amount_minor, period_start=subscription.current_period_start,
        period_end=subscription.current_period_end, now=now,
    )


def _plan_change_credit(subscription: Subscription | None, price: PlanPrice, now: datetime) -> int:
    if (
        subscription is not None
        and subscription.plan_price_id == price.pk
        and effective_status(subscription, now=now) == SubscriptionStatus.ACTIVE
    ):
        raise fail(Err.PLAN_CHANGE_NOT_ALLOWED, message="You are already subscribed to this plan.")
    return compute_plan_change_credit(subscription, price, now=now)


def _resolve_price(plan: Plan, currency: str, interval: str) -> PlanPrice:
    try:
        interval = BillingInterval(interval).value
    except ValueError:
        raise fail(
            Err.PLAN_PRICE_NOT_FOUND, plan=plan.name,
            currency=currency, interval=str(interval),
        ) from None
    price = PlanPrice.objects.filter(
        plan=plan, currency=currency, interval=interval, is_active=True
    ).first()
    if price is None:
        raise fail(
            Err.PLAN_PRICE_NOT_FOUND, plan=plan.name, currency=currency, interval=interval
        )
    return price


# --------------------------------------------------------------------------- #
# Idempotency and validation
# --------------------------------------------------------------------------- #
def _replay(existing: PaymentTransaction, plan: Plan, price: PlanPrice) -> CheckoutSession:
    if existing.plan_id != plan.pk or existing.plan_price_id != price.pk:
        raise fail(
            Err.DUPLICATE_REQUEST,
            message="This idempotency key was already used for a different request."
        )
    if existing.status == Status.PENDING and existing.checkout_url:
        return CheckoutSession(
            authorization_url=existing.checkout_url, reference=existing.reference
        )
    if existing.status == Status.PENDING:
        raise fail(
            Err.DUPLICATE_REQUEST,
            message="This request is still being processed.", retryable=True
        )
    if existing.status == Status.SUCCESS:
        raise fail(Err.DUPLICATE_REQUEST, message="This payment has already completed.")
    # Failed attempts are never replayed: a retry needs a new idempotency key.
    raise fail(
        Err.CHECKOUT_FAILED,
        message="This attempt failed. Start a new checkout.",
        reference=existing.reference
    )


def _mark_failed(payment: PaymentTransaction, code: str, message: str) -> None:
    PaymentTransaction.objects.filter(pk=payment.pk, status=Status.PENDING).update(
        status=Status.FAILED,
        failure_code=code[:100],
        failure_message=message[:500],
        updated_at=timezone.now(),
    )


def _validate_callback_url(url: str) -> None:
    """Allow-list the redirect target, or the checkout becomes an open redirect."""
    parts = urlsplit(url or "")
    allowed = set(getattr(settings, "BILLING_ALLOWED_CALLBACK_HOSTS", ()))
    scheme_ok = parts.scheme == "https" or (settings.DEBUG and parts.scheme == "http")
    if not (scheme_ok and parts.hostname and parts.hostname in allowed):
        raise fail(Err.INVALID_CALLBACK_URL)
