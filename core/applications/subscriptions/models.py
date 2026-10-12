from typing import ClassVar

import auto_prefetch
from django.db import models
from django.db.models import CASCADE
from django.db.models import PROTECT
from django.db.models import CharField
from django.db.models import DateTimeField
from django.db.models import OneToOneField
from django.db.models.constraints import UniqueConstraint
from django.utils.translation import gettext_lazy as _

from core.applications.subscriptions.queryset import PaymentTransactionManager
from core.applications.subscriptions.queryset import PlanManager
from core.applications.subscriptions.queryset import SubscriptionManager
from core.helper.enums import PaymentTransactionStatus
from core.helper.enums import SubscriptionStatus
from core.helper.models import TimeBasedModel


class Plan(TimeBasedModel):
    """
    A billable plan tier.

    Numeric limits live directly on the plan since the PRD requires them
    to be enforced server-side (max users, products, locations, etc.).

    Boolean capability toggles such as API access and custom templates
    are handled separately through Feature and PlanFeature.
    """

    name = models.CharField(max_length=50, unique=True)
    description = models.TextField(blank=True, null=True)

    price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
    )

    billing_period_days = models.PositiveIntegerField(
        default=30,
        help_text=_("Length of one billing cycle, in days."),
    )

    trial_period_days = models.PositiveIntegerField(default=0)

    # Server-enforced limits.
    # null = unlimited, according to the project convention.
    max_users = models.PositiveIntegerField(null=True, blank=True)
    max_products = models.PositiveIntegerField(null=True, blank=True)
    max_locations = models.PositiveIntegerField(null=True, blank=True)
    max_transactions_per_month = models.PositiveIntegerField(
        null=True,
        blank=True,
    )
    storage_limit_mb = models.PositiveIntegerField(null=True, blank=True)

    is_active = models.BooleanField(default=True)
    objects = PlanManager()

    class Meta(auto_prefetch.Model.Meta):
        verbose_name = _("Plan")
        verbose_name_plural = _("Plans")
        ordering = ["price"]

    def __str__(self):
        return self.name


class Feature(TimeBasedModel):
    """
    A togglable capability, for example:

    - API_ACCESS
    - CUSTOM_TEMPLATES
    - UNLIMITED_INVOICES
    - PRIORITY_SUPPORT
    """

    code = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True, null=True)

    class Meta(auto_prefetch.Model.Meta):
        verbose_name = _("Feature")
        verbose_name_plural = _("Features")
        ordering = ["name"]

    def __str__(self):
        return self.name


class PlanFeature(TimeBasedModel):
    """
    Associates a feature with a plan and determines whether
    that feature is enabled for the plan.
    """

    plan = auto_prefetch.ForeignKey(
        "subscriptions.Plan",
        on_delete=models.CASCADE,
        related_name="plan_features",
    )

    feature = auto_prefetch.ForeignKey(
        "subscriptions.Feature",
        on_delete=models.CASCADE,
        related_name="feature_plans",
    )

    enabled = models.BooleanField(default=True)

    class Meta(auto_prefetch.Model.Meta):
        verbose_name = _("Plan Feature")
        verbose_name_plural = _("Plan Features")

        constraints: ClassVar = [
            UniqueConstraint(
                fields=["plan", "feature"],
                name="unique_plan_feature",
            ),
        ]

    def __str__(self):
        return f"{self.feature.name} on {self.plan.name}"


class Subscription(TimeBasedModel):
    """
    Represents an organization's actual subscription lifecycle.

    Plan is the catalog item, while Subscription stores the organization's
    active/trialing/cancelled subscription state.
    """

    organization = OneToOneField(
        "users.Organization",
        on_delete=CASCADE,
        related_name="subscription",
    )

    plan = auto_prefetch.ForeignKey(
        "subscriptions.Plan",
        on_delete=PROTECT,
        related_name="subscriptions",
    )

    status = CharField(
        max_length=20,
        choices=SubscriptionStatus.choices,
        default=SubscriptionStatus.TRIALING,
    )

    trial_ends_at = DateTimeField(
        null=True,
        blank=True,
    )

    current_period_start = DateTimeField(
        null=True,
        blank=True,
    )

    current_period_end = DateTimeField(
        null=True,
        blank=True,
    )

    canceled_at = DateTimeField(
        null=True,
        blank=True,
    )
    objects = SubscriptionManager()


    class Meta(auto_prefetch.Model.Meta):
        verbose_name = _("Subscription")
        verbose_name_plural = _("Subscriptions")

    def __str__(self):
        return f"{self.organization.name} — {self.plan.name} ({self.status})"



class PaymentTransaction(TimeBasedModel):
    """
    One row per checkout attempt: the audit trail PRD §31 requires, extended
    to billing (immutable once terminal — see admin.py), and the idempotency
    key the webhook handler keys off of.

    `reference` is generated by us at checkout time (never by Paystack) and
    is what ties a webhook event back to this row; its uniqueness is what
    makes webhook processing idempotent under Paystack's at-least-once
    delivery (retries on anything but a 200).
    """

    organization = auto_prefetch.ForeignKey(
        "users.Organization", on_delete=models.CASCADE, related_name="payment_transactions"
    )
    plan = auto_prefetch.ForeignKey(
        "subscriptions.Plan", on_delete=models.PROTECT, related_name="payment_transactions"
    )
    initiated_by = auto_prefetch.ForeignKey(
        "users.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="payment_transactions"
    )

    provider = models.CharField(max_length=20, default="paystack")
    reference = models.CharField(max_length=100, unique=True, db_index=True)
    status = models.CharField(max_length=20, choices=PaymentTransactionStatus.choices, default=PaymentTransactionStatus.PENDING)

    amount = models.DecimalField(max_digits=12, decimal_places=2, help_text=_("Major currency unit (e.g. Naira, not kobo)."))
    currency = models.CharField(max_length=3)

    # The full webhook payload, kept verbatim — audit trail and the only way
    # to debug a disputed charge without calling Paystack support.
    raw_webhook_payload = models.JSONField(default=dict, blank=True)

    paid_at = models.DateTimeField(null=True, blank=True)

    objects = PaymentTransactionManager()


    class Meta(auto_prefetch.Model.Meta):
        verbose_name = _("Payment Transaction")
        verbose_name_plural = _("Payment Transactions")
        ordering = ["-created_at"]
        indexes: ClassVar = [models.Index(fields=["organization", "status"])]

    def __str__(self):
        return f"{self.reference} ({self.status})"
