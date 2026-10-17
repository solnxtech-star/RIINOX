from __future__ import annotations

import json
import uuid
from datetime import timedelta
from typing import TYPE_CHECKING

from django.core.exceptions import ImproperlyConfigured
from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.utils import timezone

from core.applications.subscriptions.errors import SubscriptionErrorCode as Err
from core.applications.subscriptions.errors import fail
from core.applications.subscriptions.models import PaymentTransaction
from core.applications.subscriptions.models import PaymentTransactionStatus
from core.applications.subscriptions.models import Plan
from core.applications.subscriptions.models import PlanFeature
from core.applications.subscriptions.models import Subscription
from core.applications.subscriptions.providers import paystack
from core.helper.enums import SubscriptionStatus

if TYPE_CHECKING:
    from core.applications.users.models import Organization
    from core.applications.users.models import User

__all__ = [
    "DEFAULT_PLAN_NAME",
    "GOOD_STANDING",
    "PLAN_LIMIT_FIELDS",
    "cancel_subscription",
    "check_plan_limit",
    "effective_status",
    "ensure_subscription_active",
    "get_default_plan",
    "get_plan_limit",
    "get_subscription",
    "handle_paystack_webhook",
    "initiate_plan_checkout",
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
    The stored status, except that a trial past its end date counts as
    EXPIRED. Nothing flips that status automatically, so expiry is derived
    on read (the same approach as invitation expiry). Keep in exact sync
    with SubscriptionQuerySet.in_good_standing(), which is this same rule
    expressed as a DB filter for bulk use.
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


def check_plan_limit(
    organization: Organization,
    limit_field: str,
    current_count: int,
    increment: int = 1,
) -> None:
    """
    Assert that adding `increment` to `current_count` does not violate `limit_field`.
    If limit is None (unlimited), passes.
    Raises BusinessRuleError (PLAN_LIMIT_REACHED) if limit would be exceeded.
    """
    limit = get_plan_limit(organization, limit_field)
    if limit is not None and (current_count + increment - 1) >= limit:
        friendly = limit_field.replace("max_", "").replace("_", " ")
        raise fail(
            Err.PLAN_LIMIT_REACHED,
            message=f"Your plan allows up to {limit} {friendly}. Upgrade your plan to add more.",
            limit_field=limit_field,
            limit=limit,
            current=current_count,
        )


def ensure_subscription_active(organization: Organization) -> None:
    """Asserts that the organization has an active or trialing subscription."""
    subscription = get_subscription(organization)
    if subscription is None:
        raise fail(Err.SUBSCRIPTION_REQUIRED)
    if not is_in_good_standing(subscription):
        raise fail(
            Err.SUBSCRIPTION_REQUIRED,
            message="Your subscription is expired or inactive. Please renew or upgrade to continue.",
        )


# --------------------------------------------------------------------------- #
# Checkout
# --------------------------------------------------------------------------- #
def initiate_plan_checkout(
    *, organization: Organization, plan: Plan, user: User, callback_url: str
) -> paystack.CheckoutSession:
    """
    Start a Paystack payment for `organization` to (re)subscribe to `plan`.
    Returns a hosted checkout URL to redirect the browser to — card details
    never touch this API; the subscription activates only once Paystack
    confirms payment via webhook, not from this function's return value.

    Revalidates `plan` and `organization.currency` even though the usual
    caller (a serializer whose field queryset is Plan.objects.active()) has
    already checked them: a service function is a boundary callable from
    anywhere, not only from that one view — the same defensive-reread this
    codebase already applies elsewhere (OrganizationProfile at signup).

    Each call creates a NEW PaymentTransaction with its own reference. An
    abandoned checkout simply leaves its row PENDING — visible to support via
    PaymentTransaction.objects.stale(), never silently reused by a retry.
    """
    if not plan.is_active:
        raise fail(Err.PLAN_NOT_AVAILABLE, plan=plan.name)
    if plan.price <= 0:
        raise fail(Err.PLAN_IS_FREE, plan=plan.name)
    if organization.currency not in paystack.SUPPORTED_CURRENCIES:
        raise fail(Err.UNSUPPORTED_CURRENCY, currency=organization.currency)

    reference = f"riinox-sub-{uuid.uuid4().hex}"
    transaction_row = PaymentTransaction.objects.create(
        organization=organization,
        plan=plan,
        initiated_by=user,
        reference=reference,
        amount=plan.price,
        currency=organization.currency,
    )

    try:
        return paystack.initialize_transaction(
            email=user.email,
            amount=plan.price,
            currency=organization.currency,
            reference=reference,
            callback_url=callback_url,
            metadata={"organization_id": str(organization.pk), "plan_id": str(plan.pk)},
        )
    except paystack.PaystackError as exc:
        # Recorded either way (see the PENDING-row note above); FAILED here
        # because we know for certain this attempt never reached Paystack cleanly.
        transaction_row.status = PaymentTransactionStatus.FAILED
        transaction_row.save(update_fields=["status", "updated_at"])
        raise fail(Err.CHECKOUT_FAILED, message=str(exc), provider="paystack", reference=reference) from exc


# --------------------------------------------------------------------------- #
# Webhook processing
# --------------------------------------------------------------------------- #
def handle_paystack_webhook(*, payload: bytes, signature: str | None) -> None:
    """
    Entry point for POST /webhooks/paystack/. See the module docstring for
    why this stays synchronous and minimal.

    PaystackSignatureInvalid is intentionally left to propagate as-is rather
    than wrapped in fail(): it isn't a business-rule failure with a
    SubscriptionErrorCode, it's "this request isn't genuinely from Paystack"
    — the webhook view handles that exception directly (401, no retry-loop
    encouragement). Everything past the signature check is a verified,
    genuine Paystack event, which is why nothing after this point raises at
    all: an event type we don't act on, or a reference we don't recognise,
    is a silent no-op, not an error.
    """
    paystack.verify_webhook_signature(payload, signature)  # raises PaystackSignatureInvalid

    event = json.loads(payload)
    if event.get("event") == "charge.success":
        _apply_successful_payment(event.get("data") or {})
    # else: charge.failed, subscription.disable, etc. — nothing actionable
    # yet. Add a branch here as each becomes actionable; an unrecognised
    # event type must stay a silent no-op, since Paystack adding a new event
    # type should never become a breaking change on our side.


@transaction.atomic
def _apply_successful_payment(data: dict) -> None:
    reference = data.get("reference")
    if not reference:
        return  # malformed payload; nothing to key off — drop it rather than guess

    # The atomic, conditional UPDATE *is* the concurrency control. Paystack's
    # delivery is at-least-once, so the same event WILL arrive more than
    # once; two concurrent deliveries race on this one UPDATE, and only the
    # delivery that actually flips PENDING -> SUCCESS proceeds past it. No
    # row lock needed here — unlike the Subscription write below — because a
    # conditional UPDATE is itself atomic at the database level.
    updated = PaymentTransaction.objects.for_reference(reference).pending().update(
        status=PaymentTransactionStatus.SUCCESS, paid_at=timezone.now(), raw_webhook_payload=data
    )
    if not updated:
        return  # already processed (duplicate delivery), or an unknown reference — either way, a no-op

    transaction_row = PaymentTransaction.objects.with_detail().get(reference=reference)
    _activate_subscription(transaction_row, data)
    # audit.record(...)  # subscription.payment_succeeded — hook in your audit module here


def _activate_subscription(transaction_row: PaymentTransaction, webhook_data: dict) -> None:
    """
    Updates the organization's EXISTING Subscription row in place.
    start_subscription() is deliberately not reused here: it raises
    SUBSCRIPTION_ALREADY_EXISTS by design (every organization gets one at
    creation), so activating a purchased plan is an update, not a creation.
    Locked (see SubscriptionQuerySet.locked) since a payment webhook and a
    cancel request could in principle race on the same row.
    """
    now = timezone.now()
    plan = transaction_row.plan
    customer = webhook_data.get("customer") or {}
    authorization = webhook_data.get("authorization") or {}

    subscription = Subscription.objects.locked().get(organization=transaction_row.organization)
    subscription.plan = plan
    subscription.status = SubscriptionStatus.ACTIVE
    subscription.trial_ends_at = None
    subscription.current_period_start = now
    subscription.current_period_end = now + timedelta(days=plan.billing_period_days)
    subscription.canceled_at = None
    subscription.cancel_at_period_end = False
    subscription.paystack_customer_code = customer.get("customer_code", "")
    subscription.paystack_authorization_code = authorization.get("authorization_code", "")
    subscription.save()


# --------------------------------------------------------------------------- #
# Cancellation
# --------------------------------------------------------------------------- #
@transaction.atomic
def cancel_subscription(*, organization: Organization, immediately: bool = False) -> Subscription:
    """
    Graceful by default: access continues until current_period_end, renewal
    just won't happen (a periodic job flips status -> CANCELED once that
    date passes — not built yet, matches effective_status's "derived on
    read" approach to expiry rather than needing a scheduler for this one
    case too). Pass immediately=True to revoke access right now instead
    (fraud, refund).
    """
    try:
        subscription = Subscription.objects.locked().get(organization=organization)
    except ObjectDoesNotExist:
        raise fail(Err.SUBSCRIPTION_REQUIRED) from None

    if immediately:
        subscription.status = SubscriptionStatus.CANCELED
        subscription.canceled_at = timezone.now()
    else:
        subscription.cancel_at_period_end = True

    subscription.save(update_fields=["status", "canceled_at", "cancel_at_period_end", "updated_at"])
    # audit.record(...)  # subscription.canceled
    return subscription
