from __future__ import annotations

from datetime import timedelta

import auto_prefetch
from django.db.models import Prefetch
from django.db.models import Q
from django.utils import timezone


# ---------------------------------------------------------------------------
# Plans
# ---------------------------------------------------------------------------
class PlanQuerySet(auto_prefetch.QuerySet):
    """QuerySet for Plan objects."""

    def active(self):
        """Return only active plans."""

        return self.filter(is_active=True)

    def with_features(self):
        """Preload each plan's enabled features in one extra query, not one per plan."""
        from core.applications.subscriptions.models import PlanFeature

        enabled = PlanFeature.objects.filter(enabled=True).select_related("feature")
        return self.prefetch_related(Prefetch("plan_features", queryset=enabled))

    def ordered(self):
        return self.order_by("price")


# ---------------------------------------------------------------------------
# Subscriptions
# ---------------------------------------------------------------------------
class SubscriptionQuerySet(auto_prefetch.QuerySet):
    def with_plan(self):
        return self.select_related("plan", "organization")

    def for_organization(self, organization):
        return self.filter(organization=organization)

    def locked(self):
        """
        select_for_update(), named for intent at the call site. Both
        _activate_subscription (a payment just succeeded) and
        cancel_subscription mutate a single organization's one Subscription
        row, and either could in principle race against the other — this
        makes "this call site takes a row lock" visible in services.py
        without needing a comment every time it's used.
        """
        return self.select_for_update()

    def in_good_standing(self):
        """
        The DB-level equivalent of services.is_in_good_standing(), for
        listing/counting across many organizations (an admin view, a
        billing-health dashboard, a future reconciliation job) without
        pulling every row into Python to call the per-object check — that
        works fine for one subscription, not for "how many of our N
        organizations are current." Must stay in exact sync with
        effective_status()'s derived-expiry rule: a TRIALING row only
        counts once its trial_ends_at is still in the future.
        """
        from core.helper.enums import SubscriptionStatus

        now = timezone.now()
        return self.filter(
            Q(status=SubscriptionStatus.ACTIVE)
            | Q(status=SubscriptionStatus.TRIALING, trial_ends_at__gt=now)
        )

    def trials_ending_within(self, days: int):
        """Candidates for a "your trial ends soon" reminder job — not built yet, but this is its query."""
        from core.helper.enums import SubscriptionStatus

        now = timezone.now()
        return self.filter(
            status=SubscriptionStatus.TRIALING,
            trial_ends_at__gt=now,
            trial_ends_at__lte=now + timedelta(days=days),
        )


# ---------------------------------------------------------------------------
# Payment transactions
# ---------------------------------------------------------------------------
class PaymentTransactionQuerySet(auto_prefetch.QuerySet):
    def for_organization(self, organization):
        return self.filter(organization=organization)

    def for_reference(self, reference: str):
        return self.filter(reference=reference)

    def pending(self):
        """
        Transactions that have been initiated but not yet completed (success or failure).
        """
        from core.applications.subscriptions.models import PaymentTransactionStatus

        return self.filter(status=PaymentTransactionStatus.PENDING)

    def stale(self, *, older_than: timedelta = timedelta(hours=1)):
        """
        PENDING rows past a reasonable checkout window — abandoned carts,
        not failures (a real failure gets marked FAILED immediately by
        initiate_plan_checkout). Nothing currently calls this: it's the
        query a future `mark_abandoned_transactions` cleanup command needs,
        kept here so that command is a few lines of plumbing around an
        already-correct, already-tested filter rather than a query someone
        has to get right under pressure during an incident.
        """
        from core.applications.subscriptions.models import PaymentTransactionStatus

        return self.filter(status=PaymentTransactionStatus.PENDING, created_at__lt=timezone.now() - older_than)

    def with_detail(self):
        return self.select_related("organization", "plan", "initiated_by")


PlanManager = auto_prefetch.Manager.from_queryset(PlanQuerySet)
SubscriptionManager = auto_prefetch.Manager.from_queryset(SubscriptionQuerySet)
PaymentTransactionManager = auto_prefetch.Manager.from_queryset(PaymentTransactionQuerySet)
