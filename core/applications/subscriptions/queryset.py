from __future__ import annotations

from datetime import timedelta

import auto_prefetch
from django.conf import settings
from django.db.models import Exists
from django.db.models import OuterRef
from django.db.models import Prefetch
from django.db.models import Q
from django.utils import timezone

from core.helper.enums import BillingPaymentMethodStatus
from core.helper.enums import PaymentTransactionKind
from core.helper.enums import PaymentTransactionStatus
from core.helper.enums import SubscriptionStatus

DEFAULT_GRACE_PERIOD_DAYS = 7
DEFAULT_RENEWAL_LEEWAY_HOURS = 6


def billing_grace_period() -> timedelta:
    """
    How long a PAST_DUE subscription keeps access while renewal retries run.
    """
    return timedelta(
        days=getattr(
            settings,
            "BILLING_GRACE_PERIOD_DAYS",
            DEFAULT_GRACE_PERIOD_DAYS
        )
    )


def billing_renewal_leeway() -> timedelta:
    """
    How long past period end an ACTIVE paid subscription
    stays in good standing while its renewal settles.
    """
    return timedelta(
        hours=getattr(
            settings,
            "BILLING_RENEWAL_LEEWAY_HOURS",
            DEFAULT_RENEWAL_LEEWAY_HOURS
        )
    )


# ---------------------------------------------------------------------------
# Plans
# ---------------------------------------------------------------------------
class PlanQuerySet(auto_prefetch.QuerySet):
    def active(self):
        return self.filter(is_active=True)

    def public(self):
        return self.filter(is_public=True)

    def ordered(self):
        return self.order_by("sort_order", "name")

    def with_features(self):
        """Preload each plan's enabled features in one extra query, not one per plan."""
        from core.applications.subscriptions.models import PlanFeature

        enabled = PlanFeature.objects.filter(enabled=True).select_related("feature")
        return self.prefetch_related(Prefetch("plan_features", queryset=enabled))

    def with_prices(self):
        """Preload active prices in one extra query."""
        from core.applications.subscriptions.models import PlanPrice

        return self.prefetch_related(
            Prefetch("prices", queryset=PlanPrice.objects.filter(is_active=True)),
        )

    def available_in(self, currency: str):
        """
        Plans an organization billing in `currency` can pick: active, public, and
        either free or priced in that currency. EXISTS rather than a join, so no
        DISTINCT is needed.
        """
        from core.applications.subscriptions.models import PlanPrice

        priced = PlanPrice.objects.filter(plan=OuterRef("pk"), currency=currency, is_active=True)
        return self.active().public().filter(Q(is_free=True) | Exists(priced))


# ---------------------------------------------------------------------------
# Subscriptions
# ---------------------------------------------------------------------------
class SubscriptionQuerySet(auto_prefetch.QuerySet):
    def with_plan(self):
        return self.select_related("plan", "plan_price")

    def with_billing(self):
        return self.select_related("plan", "plan_price", "default_payment_method")

    def for_organization(self, organization):
        return self.filter(organization=organization)

    def locked(self, *, skip_locked: bool = False):
        """
        select_for_update(), named for intent at the call site. `of=("self",)`
        locks only the subscription row: with select_related joins on nullable
        FKs, PostgreSQL refuses to lock the nullable side of an outer join.
        `skip_locked=True` is for batch sweeps, so workers never queue on each
        other's rows. Only valid inside transaction.atomic().
        """
        return self.select_for_update(of=("self",), skip_locked=skip_locked)

    # -- Standing ----------------------------------------------------------
    def in_good_standing(self):
        """
        DB-level equivalent of services.is_in_good_standing(), for bulk use
        (admin lists, billing-health dashboards) without loading rows into
        Python. Must stay in exact sync with effective_status(); a test should
        assert the two agree across every status/date combination.

        - ACTIVE: free plans, or a paid period that ended less than the renewal
        leeway ago (so a renewal still settling never locks a paying customer out)
        - TRIALING: trial not yet over
        - PAST_DUE: still inside the grace period while renewals are retried
        """
        now = timezone.now()
        return self.filter(
            Q(status=SubscriptionStatus.ACTIVE)
            & (
                Q(plan__is_free=True)
                | Q(current_period_end__isnull=True)
                | Q(current_period_end__gt=now - billing_renewal_leeway())
            )
            | Q(status=SubscriptionStatus.TRIALING, trial_ends_at__gt=now)
            | Q(status=SubscriptionStatus.PAST_DUE, past_due_since__gt=now - billing_grace_period()),
        )

    # -- Sweeps ------------------------------------------------------------
    # Each backs one background job. Use inside transaction.atomic() as
    # `.locked(skip_locked=True)[:BATCH]` so workers split the work safely.
    def due_for_renewal(self, *, lead: timedelta = timedelta(hours=1)):
        """
        ACTIVE paid subscriptions to charge automatically: not set to cancel and
        holding a usable payment method. Charging `lead` ahead of period end, with
        the new period starting at the OLD period end, means a slightly late job
        never costs the customer access.
        """
        return self.filter(
            status=SubscriptionStatus.ACTIVE,
            cancel_at_period_end=False,
            plan_price__isnull=False,
            default_payment_method__status=BillingPaymentMethodStatus.ACTIVE,
            current_period_end__lte=timezone.now() + lead,
        ).order_by("current_period_end")

    def due_for_period_end(self):
        """
        ACTIVE paid subscriptions whose period ended and that will NOT renew: set to
        cancel, or no usable payment method. Exact complement of due_for_renewal(),
        so every ended period is handled by exactly one sweep.
        """
        return self.filter(
            Q(cancel_at_period_end=True) | ~Q(default_payment_method__status=BillingPaymentMethodStatus.ACTIVE),
            status=SubscriptionStatus.ACTIVE,
            plan_price__isnull=False,
            current_period_end__lte=timezone.now(),
        ).order_by("current_period_end")

    def due_for_retry(self):
        """PAST_DUE subscriptions whose next renewal retry is due."""
        return self.filter(
            status=SubscriptionStatus.PAST_DUE,
            plan_price__isnull=False,
            default_payment_method__status=BillingPaymentMethodStatus.ACTIVE,
            next_retry_at__lte=timezone.now(),
        ).order_by("next_retry_at")

    def grace_expired(self):
        """PAST_DUE subscriptions out of grace: access ends (-> EXPIRED)."""
        return self.filter(
            status=SubscriptionStatus.PAST_DUE,
            past_due_since__lte=timezone.now() - billing_grace_period(),
        ).order_by("past_due_since")

    def trials_expired(self):
        return self.filter(
            status=SubscriptionStatus.TRIALING,
            trial_ends_at__lte=timezone.now(),
        ).order_by("trial_ends_at")

    def trials_ending_within(self, days: int):
        """Candidates for a "your trial ends soon" reminder job."""
        now = timezone.now()
        return self.filter(
            status=SubscriptionStatus.TRIALING,
            trial_ends_at__gt=now,
            trial_ends_at__lte=now + timedelta(days=days),
        )

    def lapsed(self):
        """
        Read-only subscriptions: paid access ended and was not renewed. Feeds the
        win-back and retention jobs; `ended_at` says how long each has been lapsed.
        """
        return self.filter(status__in=(SubscriptionStatus.CANCELED, SubscriptionStatus.EXPIRED))

# ---------------------------------------------------------------------------
# Payment transactions
# ---------------------------------------------------------------------------
class PaymentTransactionQuerySet(auto_prefetch.QuerySet):
    def for_organization(self, organization):
        return self.filter(organization=organization)

    def for_subscription(self, subscription):
        return self.filter(subscription=subscription)

    def for_reference(self, reference: str):
        return self.filter(reference=reference)

    def renewals(self):
        return self.filter(kind=PaymentTransactionKind.RENEWAL)

    def checkouts(self):
        return self.filter(kind=PaymentTransactionKind.CHECKOUT)

    def pending(self):
        """Initiated but not yet completed (success or failure)."""
        return self.filter(status=PaymentTransactionStatus.PENDING)

    def reconcilable(self, *, min_age: timedelta = timedelta(minutes=10)):
        """
        PENDING rows old enough that their webhook should have arrived. The
        reconciliation job asks the provider about each one directly, which
        recovers lost webhooks. Young rows are skipped: their webhook is
        probably still on its way.
        """
        return self.pending().filter(created_at__lte=timezone.now() - min_age)

    def stale(self, *, older_than: timedelta = timedelta(hours=24)):
        """
        PENDING rows past the whole reconciliation window: abandoned checkouts,
        not failures (a clean provider rejection is marked FAILED immediately).
        Run reconciliation first, then mark whatever is still pending ABANDONED.
        """
        return self.pending().filter(created_at__lt=timezone.now() - older_than)

    def with_detail(self):
        return self.select_related(
            "organization", "plan", "plan_price", "payment_method", "initiated_by",
        )


PlanManager = auto_prefetch.Manager.from_queryset(PlanQuerySet)
SubscriptionManager = auto_prefetch.Manager.from_queryset(SubscriptionQuerySet)
PaymentTransactionManager = auto_prefetch.Manager.from_queryset(PaymentTransactionQuerySet)
