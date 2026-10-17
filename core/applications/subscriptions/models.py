import datetime
from typing import ClassVar

import auto_prefetch
from django.core.validators import MinValueValidator
from django.core.validators import RegexValidator
from django.db import models
from django.db.models import Q
from django.db.models.functions import Upper
from django.utils.translation import gettext_lazy as _

from core.applications.subscriptions.queryset import PaymentTransactionManager
from core.applications.subscriptions.queryset import PlanManager
from core.applications.subscriptions.queryset import SubscriptionManager
from core.helper.enums import BillingInterval
from core.helper.enums import BillingPaymentMethodStatus
from core.helper.enums import PaymentTransactionKind
from core.helper.enums import PaymentTransactionStatus
from core.helper.enums import SubscriptionEventType
from core.helper.enums import SubscriptionStatus
from core.helper.enums import UsageMetric
from core.helper.enums import WebhookEventStatus
from core.helper.fields import EncryptedJSONField
from core.helper.fields import EncryptedTextField
from core.helper.models import TimeBasedModel

# period_start used for running totals (users, products, ...) that never reset.
LIFETIME_PERIOD = datetime.date(1970, 1, 1)

CURRENCY_VALIDATOR = RegexValidator(
    r"^[A-Z]{3}$",
    _("Use a 3-letter uppercase ISO 4217 currency code."),
)


class Plan(TimeBasedModel):
    """
    A catalog tier: identity, capabilities and limits. Prices live in PlanPrice.

    Limits (PRD §10, enforced server-side): NULL means unlimited, 0 means none
    allowed. There is no "0 = unlimited" shortcut, so a plan like
    "Free: 0 locations" is expressible.

    Boolean capabilities (API access, custom templates, integrations, ...) are
    modelled separately through Feature / PlanFeature.
    """

    code = models.SlugField(
        max_length=50,
        unique=True,
        help_text=_("Stable identifier; never changes after creation."),
    )
    name = models.CharField(max_length=50, unique=True)
    description = models.TextField(blank=True)

    is_free = models.BooleanField(default=False)
    is_default = models.BooleanField(
        default=False,
        help_text=_("Assigned to every new organization. At most one plan."),
    )
    is_public = models.BooleanField(
        default=True,
        help_text=_("False = custom/enterprise plan, not listed publicly."),
    )
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    trial_period_days = models.PositiveIntegerField(default=0)

    max_users = models.PositiveIntegerField(null=True, blank=True)
    max_products = models.PositiveIntegerField(null=True, blank=True)
    max_locations = models.PositiveIntegerField(null=True, blank=True)
    max_transactions_per_month = models.PositiveIntegerField(null=True, blank=True)
    storage_limit_mb = models.PositiveIntegerField(null=True, blank=True)

    objects = PlanManager()

    class Meta(auto_prefetch.Model.Meta):
        verbose_name = _("Plan")
        verbose_name_plural = _("Plans")
        ordering: ClassVar = ["sort_order", "name"]
        constraints: ClassVar = [
            models.UniqueConstraint(
                fields=["is_default"],
                condition=Q(is_default=True),
                name="single_default_plan",
            ),
        ]

    def __str__(self):
        return self.name


class PlanPrice(TimeBasedModel):
    """
    What a plan costs in one currency and billing interval.

    Treated as immutable: to change a price, deactivate the row and create a
    new one, so existing subscribers keep the price they signed up at
    (grandfathering). Free plans have no PlanPrice rows.
    """

    plan = auto_prefetch.ForeignKey(
        "subscriptions.Plan",
        on_delete=models.CASCADE,
        related_name="prices",
    )
    currency = models.CharField(max_length=3, validators=[CURRENCY_VALIDATOR])
    interval = models.CharField(
        max_length=10,
        choices=BillingInterval.choices,
        default=BillingInterval.MONTHLY,
    )
    amount_minor = models.BigIntegerField(
        validators=[MinValueValidator(1)],
        help_text=_("Minor currency units (kobo, cents)."),
    )
    is_active = models.BooleanField(default=True)

    class Meta(auto_prefetch.Model.Meta):
        verbose_name = _("Plan price")
        verbose_name_plural = _("Plan prices")
        constraints: ClassVar = [
            models.UniqueConstraint(
                fields=["plan", "currency", "interval"],
                condition=Q(is_active=True),
                name="unique_active_plan_price",
            ),
            models.CheckConstraint(
                condition=Q(amount_minor__gt=0),
                name="plan_price_positive",
            ),
            models.CheckConstraint(
                condition=Q(currency=Upper("currency")),
                name="plan_price_currency_upper",
            ),
        ]

    def __str__(self):
        return f"{self.plan.name} {self.currency} {self.amount_minor} / {self.interval}"



class Feature(TimeBasedModel):
    """
    A togglable capability, for example:

    - API_ACCESS
    - CUSTOM_TEMPLATES
    - INTEGRATIONS
    - PRIORITY_SUPPORT
    """

    code = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True, null=True)

    class Meta(auto_prefetch.Model.Meta):
        verbose_name = _("Feature")
        verbose_name_plural = _("Features")
        ordering: ClassVar = ["name"]

    def __str__(self):
        return self.name

class PlanFeature(TimeBasedModel):
    """Associates a feature with a plan and whether it is enabled there."""

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
        verbose_name = _("Plan feature")
        verbose_name_plural = _("Plan features")
        constraints: ClassVar = [
            models.UniqueConstraint(
                fields=["plan", "feature"],
                name="unique_plan_feature",
            ),
        ]

    def __str__(self):
        return f"{self.feature.name} on {self.plan.name}"



class Subscription(TimeBasedModel):
    """
    An organization's billing state (PRD §10: subscriptions belong to the
    organization). Plan is the catalog item; this row is the lifecycle.

    Free plans have no plan_price and no period end, so they never lapse.
    Paid plans always carry both.

    Lapse policy: when a paid subscription ends (CANCELED or EXPIRED) the
    organization becomes READ-ONLY. Its data stays visible and exportable, but
    nothing can be created until it pays again. `ended_at` records when access
    ended.
    """

    organization = auto_prefetch.OneToOneField(
        "users.Organization",
        on_delete=models.PROTECT,
        related_name="subscription",
    )
    plan = auto_prefetch.ForeignKey(
        "subscriptions.Plan",
        on_delete=models.PROTECT,
        related_name="subscriptions",
    )
    plan_price = auto_prefetch.ForeignKey(
        "subscriptions.PlanPrice",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="subscriptions",
    )

    status = models.CharField(
        max_length=20,
        choices=SubscriptionStatus.choices,
        default=SubscriptionStatus.TRIALING,
    )

    trial_ends_at = models.DateTimeField(null=True, blank=True)
    current_period_start = models.DateTimeField(null=True, blank=True)
    current_period_end = models.DateTimeField(null=True, blank=True)

    cancel_at_period_end = models.BooleanField(
        default=False,
        help_text=_("If true, access continues until current_period_end, then ends without renewal."),
    )
    canceled_at = models.DateTimeField(null=True, blank=True, help_text=_("When cancellation was requested."))
    ended_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("When paid access ended (status became CANCELED or EXPIRED). Cleared on reactivation."),
    )

    default_payment_method = auto_prefetch.ForeignKey(
        "subscriptions.BillingPaymentMethod",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    # Dunning state for failed automatic renewals.
    renewal_attempts = models.PositiveSmallIntegerField(default=0)
    next_retry_at = models.DateTimeField(null=True, blank=True)
    past_due_since = models.DateTimeField(null=True, blank=True)

    objects = SubscriptionManager()

    class Meta(auto_prefetch.Model.Meta):
        verbose_name = _("Subscription")
        verbose_name_plural = _("Subscriptions")
        indexes: ClassVar = [
            models.Index(fields=["status", "current_period_end"], name="sub_status_period_end_idx"),
            models.Index(fields=["status", "next_retry_at"], name="sub_status_retry_idx"),
        ]
        constraints: ClassVar = [
            models.CheckConstraint(
                condition=Q(current_period_start__isnull=True)
                | Q(current_period_end__isnull=True)
                | Q(current_period_end__gt=models.F("current_period_start")),
                name="subscription_period_ordered",
            ),
        ]

    def __str__(self):
        return f"{self.organization_id} {self.plan_id} ({self.status})"


class SubscriptionEvent(TimeBasedModel):
    """
    Append-only history of every subscription state change
    (PRD §31 immutable audit, applied to billing).
    """

    organization = auto_prefetch.ForeignKey(
        "users.Organization",
        on_delete=models.PROTECT,
        related_name="subscription_events",
    )
    subscription = auto_prefetch.ForeignKey(
        "subscriptions.Subscription",
        on_delete=models.PROTECT,
        related_name="events",
    )
    event_type = models.CharField(max_length=30, choices=SubscriptionEventType.choices)
    from_status = models.CharField(max_length=20, blank=True)
    to_status = models.CharField(max_length=20, blank=True)
    from_plan = auto_prefetch.ForeignKey(
        "subscriptions.Plan",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )
    to_plan = auto_prefetch.ForeignKey(
        "subscriptions.Plan",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )
    transaction = auto_prefetch.ForeignKey(
        "subscriptions.PaymentTransaction",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )
    actor = auto_prefetch.ForeignKey(
        "users.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    reason = models.CharField(max_length=255, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta(auto_prefetch.Model.Meta):
        verbose_name = _("Subscription event")
        verbose_name_plural = _("Subscription events")
        ordering: ClassVar = ["-created_at"]
        indexes: ClassVar = [
            models.Index(fields=["subscription", "-created_at"], name="subevent_sub_created_idx"),
            models.Index(fields=["organization", "-created_at"], name="subevent_org_created_idx"),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise RuntimeError("SubscriptionEvent is append-only.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise RuntimeError("SubscriptionEvent is append-only.")

    def __str__(self):
        return f"{self.event_type} ({self.subscription_id})"



class BillingPaymentMethod(TimeBasedModel):
    """
    A reusable payment token captured at checkout and charged for automatic
    renewals. Named "Billing..." because PRD §26 payment methods (cash, POS,
    bank transfer) belong to the organization's own sales and will live in a
    separate payments app.
    """

    organization = auto_prefetch.ForeignKey(
        "users.Organization",
        on_delete=models.PROTECT,
        related_name="billing_payment_methods",
    )
    provider = models.CharField(max_length=20)
    provider_customer_id = models.CharField(max_length=100, blank=True)
    customer_email = models.EmailField(
        help_text=_(
            "Email the token was created under. Providers only allow charging with the same email.",

        ),
    )
    token = EncryptedTextField(
        help_text=_("Reusable charge token. Encrypted at rest; never log or expose.")
    )
    fingerprint = models.CharField(
        max_length=100,
        blank=True,
        help_text=_("Provider card fingerprint; prevents saving the same card twice."),
    )

    brand = models.CharField(max_length=30, blank=True)
    last4 = models.CharField(max_length=4, blank=True)
    exp_month = models.PositiveSmallIntegerField(null=True, blank=True)
    exp_year = models.PositiveSmallIntegerField(null=True, blank=True)

    status = models.CharField(
        max_length=10,
        choices=BillingPaymentMethodStatus.choices,
        default=BillingPaymentMethodStatus.ACTIVE,
    )
    created_by = auto_prefetch.ForeignKey(
        "users.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta(auto_prefetch.Model.Meta):
        verbose_name = _("Billing payment method")
        verbose_name_plural = _("Billing payment methods")
        constraints: ClassVar = [
            models.UniqueConstraint(
                fields=["organization", "provider", "fingerprint"],
                condition=~Q(fingerprint=""),
                name="unique_org_billing_method_fingerprint",
            ),
        ]

    def __str__(self):
        return f"{self.brand} ••••{self.last4}".strip()


class PaymentTransaction(TimeBasedModel):
    """
    One row per billing payment attempt, checkout or renewal. This is the
    audit trail PRD §31 requires, extended to billing; immutable once terminal
    (enforced by conditional updates in services and a read-only admin).

    `reference` is generated by us (never by the provider) and ties a webhook
    back to this row; its uniqueness makes webhook processing idempotent.
    Providers restrict reference characters (Paystack: letters, digits, "-",
    ".", "="), so references use hyphens only:

        checkout: riinox-sub-{uuid.hex}
        renewal:  riinox-ren-{subscription_id.hex}-{period_end_epoch}-{attempt}
    """

    organization = auto_prefetch.ForeignKey(
        "users.Organization",
        on_delete=models.PROTECT,
        related_name="billing_transactions",
    )
    subscription = auto_prefetch.ForeignKey(
        "subscriptions.Subscription",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="payment_transactions",
    )
    plan = auto_prefetch.ForeignKey(
        "subscriptions.Plan",
        on_delete=models.PROTECT,
        related_name="payment_transactions",
    )
    plan_price = auto_prefetch.ForeignKey(
        "subscriptions.PlanPrice",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="payment_transactions",
    )
    payment_method = auto_prefetch.ForeignKey(
        "subscriptions.BillingPaymentMethod",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payment_transactions",
    )
    initiated_by = auto_prefetch.ForeignKey(
        "users.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="initiated_billing_transactions",
    )

    kind = models.CharField(
        max_length=10,
        choices=PaymentTransactionKind.choices,
        default=PaymentTransactionKind.CHECKOUT,
    )
    provider = models.CharField(max_length=20)
    reference = models.CharField(max_length=100, unique=True)  # unique already indexes it
    idempotency_key = models.CharField(
        max_length=100,
        blank=True,
        help_text=_("Client-supplied (PRD §45); dedupes double submits."),
    )
    status = models.CharField(
        max_length=10,
        choices=PaymentTransactionStatus.choices,
        default=PaymentTransactionStatus.PENDING,
    )

    amount_minor = models.BigIntegerField(help_text=_("Minor currency units (kobo, cents)."))
    currency = models.CharField(max_length=3, validators=[CURRENCY_VALIDATOR])

    period_start = models.DateTimeField(null=True, blank=True, help_text=_("Billing period this payment buys."))
    period_end = models.DateTimeField(null=True, blank=True)
    attempt_number = models.PositiveSmallIntegerField(default=1)

    failure_code = models.CharField(max_length=100, blank=True)
    failure_message = models.TextField(blank=True)

    raw_webhook_payload = models.JSONField(
        default=dict,
        blank=True,
        help_text=_("Redacted by the provider adapter; never contains reusable tokens."),
    )
    paid_at = models.DateTimeField(null=True, blank=True)

    objects = PaymentTransactionManager()

    class Meta(auto_prefetch.Model.Meta):
        verbose_name = _("Payment transaction")
        verbose_name_plural = _("Payment transactions")
        ordering: ClassVar = ["-created_at"]
        indexes: ClassVar = [
            models.Index(fields=["organization", "status"], name="paytx_org_status_idx"),
            models.Index(fields=["organization", "-created_at"], name="paytx_org_created_idx"),
            models.Index(fields=["status", "created_at"], name="paytx_status_created_idx"),
        ]
        constraints: ClassVar = [
            models.CheckConstraint(
                condition=Q(amount_minor__gt=0),
                name="paytx_amount_positive",
            ),
            models.CheckConstraint(
                condition=Q(currency=Upper("currency")),
                name="paytx_currency_upper",
            ),
            # A retried renewal job can never create a second row for the same attempt.
            models.UniqueConstraint(
                fields=["subscription", "period_end", "attempt_number"],
                condition=Q(kind=PaymentTransactionKind.RENEWAL),
                name="unique_renewal_attempt_per_period",
            ),
            models.UniqueConstraint(
                fields=["organization", "idempotency_key"],
                condition=~Q(idempotency_key=""),
                name="unique_org_idempotency_key",
            ),
        ]

    def __str__(self):
        return f"{self.reference} ({self.status})"


class WebhookEvent(TimeBasedModel):
    """
    Every provider webhook we act on, stored on receipt and processed
    afterwards, so the endpoint can answer 200 immediately and failed
    processing can be retried.

    `event_id` is computed by the provider adapter. Paystack sends no event id,
    so its adapter derives one (e.g. f"{event}:{data.id}"), falling back to a
    hash of the body.
    """

    provider = models.CharField(max_length=20)
    event_id = models.CharField(max_length=150)
    event_type = models.CharField(max_length=100)
    payload = EncryptedJSONField(
        default=dict,
        help_text=_("Encrypted at rest: may contain reusable tokens. Hidden in the admin."),
    )
    transaction = auto_prefetch.ForeignKey(
        "subscriptions.PaymentTransaction",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text=_("Set once processing resolves the event to a payment (gives it an organization context)."),
    )
    status = models.CharField(
        max_length=10,
        choices=WebhookEventStatus.choices,
        default=WebhookEventStatus.RECEIVED,
    )
    attempts = models.PositiveSmallIntegerField(default=0)
    last_error = models.TextField(blank=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta(auto_prefetch.Model.Meta):
        verbose_name = _("Webhook event")
        verbose_name_plural = _("Webhook events")
        indexes: ClassVar = [
            models.Index(fields=["status", "created_at"], name="webhook_status_created_idx"),
        ]
        constraints: ClassVar = [
            models.UniqueConstraint(
                fields=["provider", "event_id"],
                name="unique_provider_event",
            ),
        ]

    def __str__(self):
        return f"{self.provider}:{self.event_id} ({self.status})"

# --------------------------------------------------------------------------- #
# Metering
# --------------------------------------------------------------------------- #
class UsageCounter(TimeBasedModel):
    """
    Atomic usage counter backing the PRD §10 limits. Monthly metrics use the
    first of the month as period_start; running totals use LIFETIME_PERIOD.
    Limit enforcement is a conditional UPDATE on this row (no lock held across
    a request). CASCADE (unlike the billing records) because counters are
    derived data and can be rebuilt.
    """

    organization = auto_prefetch.ForeignKey(
        "users.Organization",
        on_delete=models.CASCADE,
        related_name="usage_counters",
    )
    metric = models.CharField(max_length=20, choices=UsageMetric.choices)
    period_start = models.DateField(default=LIFETIME_PERIOD)
    value = models.BigIntegerField(default=0)

    class Meta(auto_prefetch.Model.Meta):
        verbose_name = _("Usage counter")
        verbose_name_plural = _("Usage counters")
        constraints: ClassVar = [
            models.UniqueConstraint(
                fields=["organization", "metric", "period_start"],
                name="unique_usage_counter",
            ),
            models.CheckConstraint(
                condition=Q(value__gte=0),
                name="usage_counter_non_negative",
            ),
        ]

    def __str__(self):
        return f"{self.organization_id} {self.metric} {self.period_start}: {self.value}"
