from __future__ import annotations

from decimal import Decimal

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from core.applications.payment.models import InvoicePayment
from core.helper.serializer_fields import PaymentMethodField

MONEY = {"max_digits": 12, "decimal_places": 2}
MAX_REFERENCE_LENGTH = 100
MAX_NOTE_LENGTH = 500


class _PaymentPartySerializer(serializers.Serializer):
    id = serializers.CharField(read_only=True)
    name = serializers.CharField(read_only=True)


class _PaymentUserSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    name = serializers.SerializerMethodField()

    def get_name(self, user) -> str:
        return getattr(user, "name", "") or user.email


class _InvoiceRefSerializer(serializers.Serializer):
    """The invoice as it is now (not as it was when this payment was made)."""

    id = serializers.CharField(read_only=True)
    invoice_number = serializers.CharField(read_only=True)
    status = serializers.CharField(read_only=True)
    total = serializers.DecimalField(read_only=True, **MONEY)
    amount_paid = serializers.DecimalField(read_only=True, **MONEY)
    balance_due = serializers.SerializerMethodField()
    due_date = serializers.DateField(read_only=True)

    @extend_schema_field(serializers.DecimalField(**MONEY))
    def get_balance_due(self, invoice) -> str:
        balance = max(invoice.total - invoice.amount_paid, Decimal("0.00"))
        return serializers.DecimalField(**MONEY).to_representation(balance)


class PaymentSerializer(serializers.ModelSerializer):
    """A customer payment. It is also the receipt (`receipt_number`)."""

    method = serializers.CharField(source="payment.method", read_only=True)
    reference = serializers.CharField(source="payment.reference", read_only=True)
    currency = serializers.CharField(source="payment.currency", read_only=True)
    note = serializers.CharField(source="payment.description", read_only=True)
    invoice = _InvoiceRefSerializer(read_only=True)
    customer = _PaymentPartySerializer(source="invoice.customer", read_only=True)
    location = _PaymentPartySerializer(source="invoice.warehouse", read_only=True)
    received_by = _PaymentUserSerializer(source="sales_rep", read_only=True, allow_null=True)

    class Meta:
        model = InvoicePayment
        fields = [
            "id", "receipt_number", "amount", "currency", "method", "reference", "note",
            "balance_after", "invoice", "customer", "location", "received_by",
            "received_on", "is_voided",
        ]
        read_only_fields = fields


class RecordedPaymentSerializer(PaymentSerializer):
    """Response of `POST /payments/`: the payment plus the cash change to hand back."""

    change_due = serializers.DecimalField(read_only=True, **MONEY)
    replayed = serializers.BooleanField(read_only=True)

    class Meta(PaymentSerializer.Meta):
        fields = [*PaymentSerializer.Meta.fields, "change_due", "replayed"]
        read_only_fields = fields


class PaymentCreateSerializer(serializers.Serializer):
    invoice_id = serializers.UUIDField(help_text="The invoice being paid (from GET /invoices/).")
    method = PaymentMethodField()
    amount = serializers.DecimalField(
        required=False,
        allow_null=True,
        min_value=Decimal("0.01"),
        **MONEY,
        help_text=(
            "Amount received. Leave out to pay the whole balance. Cash above the balance is "
            "allowed (the extra comes back as `change_due`); any other method above the balance is refused."
        ),
    )
    reference = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        max_length=MAX_REFERENCE_LENGTH,
        help_text=(
            "Bank/POS/transfer reference. It identifies the payment: resending the same reference for the "
            "same invoice returns the original instead of paying twice. Generated when left out."
        ),
    )
    note = serializers.CharField(required=False, allow_blank=True, default="", max_length=MAX_NOTE_LENGTH)

    def validate(self, attrs):
        unknown = sorted(set(self.initial_data) - set(self.fields))
        if unknown:
            raise serializers.ValidationError({key: ["Unknown field."] for key in unknown})
        return attrs
