"""Billing API serializers (PRD §10 plans and limits, §37 module visibility, §55 billing access)."""

from __future__ import annotations

from decimal import Decimal

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from core.applications.subscriptions.models import BillingPaymentMethod
from core.applications.subscriptions.models import Plan
from core.applications.subscriptions.models import PlanPrice
from core.helper.enums import BillingInterval

LIMIT_FIELDS = {
    "users": "max_users",
    "products": "max_products",
    "locations": "max_locations",
    "transactions_per_month": "max_transactions_per_month",
    "storage_mb": "storage_limit_mb",
}


class PlanPriceSerializer(serializers.ModelSerializer):
    amount = serializers.SerializerMethodField(help_text="Display amount in major units, e.g. \"15000.00\".")

    class Meta:
        model = PlanPrice
        fields = ["currency", "interval", "amount_minor", "amount"]
        read_only_fields = fields

    @extend_schema_field(OpenApiTypes.STR)
    def get_amount(self, price: PlanPrice) -> str:
        return f"{(Decimal(price.amount_minor) / 100):.2f}"


class BillingPlanSerializer(serializers.ModelSerializer):
    """A plan of the public catalogue. Expects `prices` and `plan_features` prefetched."""

    limits = serializers.SerializerMethodField(
        help_text="Per-plan limits; null = unlimited. transactions_per_month resets monthly."
    )
    features = serializers.SerializerMethodField(help_text="Enabled feature codes, e.g. CUSTOM_TEMPLATES.")
    prices = serializers.SerializerMethodField()

    class Meta:
        model = Plan
        fields = [
            "code", "name", "description", "is_free", "trial_period_days",
            "limits", "features", "prices",
        ]
        read_only_fields = fields

    @extend_schema_field(OpenApiTypes.OBJECT)
    def get_limits(self, plan: Plan) -> dict[str, int | None]:
        return {key: getattr(plan, field) for key, field in LIMIT_FIELDS.items()}

    @extend_schema_field(serializers.ListField(child=serializers.CharField()))
    def get_features(self, plan: Plan) -> list[str]:
        return sorted(pf.feature.code for pf in plan.plan_features.all() if pf.enabled)

    @extend_schema_field(PlanPriceSerializer(many=True))
    def get_prices(self, plan: Plan) -> list[dict]:
        currency = self.context.get("currency")
        prices = [p for p in plan.prices.all() if p.is_active and (not currency or p.currency == currency)]
        return PlanPriceSerializer(prices, many=True).data


class EntitlementsSerializer(serializers.Serializer):
    """What the organization's plan allows right now; drives module visibility (PRD §37)."""

    plan_code = serializers.CharField()
    is_free = serializers.BooleanField()
    status = serializers.CharField(help_text="Effective status: trialing, active, past_due, canceled, expired.")
    access = serializers.CharField(
        help_text="full = everything allowed; read_only = the plan lapsed: reads work, writes are refused."
    )
    features = serializers.ListField(child=serializers.CharField())
    limits = serializers.DictField(child=serializers.IntegerField(allow_null=True))
    period_end = serializers.DateTimeField(allow_null=True)


class PaymentMethodSerializer(serializers.ModelSerializer):
    """A saved card / account. The reusable token is never exposed."""

    is_default = serializers.SerializerMethodField()

    class Meta:
        model = BillingPaymentMethod
        fields = ["id", "provider", "brand", "last4", "exp_month", "exp_year", "status", "is_default", "created_at"]
        read_only_fields = fields

    @extend_schema_field(OpenApiTypes.BOOL)
    def get_is_default(self, method: BillingPaymentMethod) -> bool:
        return str(method.pk) == str(self.context.get("default_method_id"))


class UsageItemSerializer(serializers.Serializer):
    used = serializers.IntegerField()
    limit = serializers.IntegerField(allow_null=True)


class BillingSummarySerializer(serializers.Serializer):
    """The billing page."""

    plan = BillingPlanSerializer()
    status = serializers.CharField()
    access = serializers.CharField()
    interval = serializers.CharField(allow_null=True)
    trial_ends_at = serializers.DateTimeField(allow_null=True)
    current_period_start = serializers.DateTimeField(allow_null=True)
    current_period_end = serializers.DateTimeField(allow_null=True)
    cancel_at_period_end = serializers.BooleanField()
    default_payment_method = PaymentMethodSerializer(allow_null=True)
    usage = serializers.DictField(child=UsageItemSerializer())


class CheckoutRequestSerializer(serializers.Serializer):
    plan = serializers.SlugField(help_text="Plan `code` from GET /billing/plans/.")
    interval = serializers.ChoiceField(choices=BillingInterval.choices)
    callback_url = serializers.URLField(
        help_text="Where the provider sends the user back. Must be an allowed HTTPS host."
    )
    idempotency_key = serializers.CharField(
        required=False, allow_blank=True, max_length=64,
        help_text="Repeat the same key to get the same checkout instead of a second one.",
    )


class CheckoutResponseSerializer(serializers.Serializer):
    authorization_url = serializers.URLField(help_text="Redirect the user here to pay.")
    reference = serializers.CharField()


class DefaultPaymentMethodSerializer(serializers.Serializer):
    payment_method_id = serializers.UUIDField()
