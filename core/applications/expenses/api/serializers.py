from __future__ import annotations

from decimal import Decimal

from rest_framework import serializers

from core.applications.expenses.models import Expense
from core.applications.warehouse.models import Warehouse
from core.helper.enums import ExpenseCategory
from core.helper.serializer_fields import PaymentMethodField

FIXED_AFTER_RECORDING = ("amount", "location", "payment_method", "currency")


class _RefSerializer(serializers.Serializer):
    id = serializers.CharField(read_only=True)
    name = serializers.CharField(read_only=True)


class _UserRefSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    name = serializers.SerializerMethodField()

    def get_name(self, user) -> str:
        return getattr(user, "name", "") or user.email


class ExpenseSerializer(serializers.ModelSerializer):
    location = _RefSerializer(read_only=True)
    category_display = serializers.CharField(source="get_category_display", read_only=True)
    recorded_by = _UserRefSerializer(read_only=True, allow_null=True)
    voided_by = _UserRefSerializer(read_only=True, allow_null=True)

    class Meta:
        model = Expense
        fields = [
            "id", "expense_number", "status", "category", "category_display", "title",
            "amount", "currency", "expense_date", "payment_method", "paid_to", "reference",
            "notes", "location", "recorded_by", "created_at", "updated_at",
            "voided_by", "voided_at", "void_reason",
        ]
        read_only_fields = fields


class ExpenseCreateSerializer(serializers.Serializer):
    location = serializers.PrimaryKeyRelatedField(
        queryset=Warehouse.objects.none(),
        help_text="Location id (from GET /locations/). Must be one of yours and active.",
    )
    category = serializers.ChoiceField(choices=ExpenseCategory.choices)
    title = serializers.CharField(max_length=150, help_text="e.g. 'Diesel for generator'.")
    amount = serializers.DecimalField(max_digits=14, decimal_places=2, min_value=Decimal("0.01"))
    payment_method = PaymentMethodField()
    expense_date = serializers.DateField(
        required=False,
        help_text="Defaults to today (organization's calendar). Can't be in the future.",
    )
    paid_to = serializers.CharField(max_length=150, required=False, allow_blank=True, default="")
    reference = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    notes = serializers.CharField(required=False, allow_blank=True, default="")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        organization = self.context.get("organization")
        if organization is not None:
            self.fields["location"].queryset = Warehouse.objects.filter(
                organization=organization, is_active=True
            )


class ExpenseUpdateSerializer(serializers.Serializer):
    """Descriptive fields only. Money facts are fixed once posted to the ledger."""

    category = serializers.ChoiceField(choices=ExpenseCategory.choices, required=False)
    title = serializers.CharField(max_length=150, required=False)
    expense_date = serializers.DateField(required=False)
    paid_to = serializers.CharField(max_length=150, required=False, allow_blank=True)
    reference = serializers.CharField(max_length=100, required=False, allow_blank=True)
    notes = serializers.CharField(required=False, allow_blank=True)

    def to_internal_value(self, data):
        if hasattr(data, "keys"):
            fixed = [field for field in FIXED_AFTER_RECORDING if field in data]
            if fixed:
                raise serializers.ValidationError(
                    {
                        field: [
                            "Can't be changed after recording. Void this expense and record a new one."
                        ]
                        for field in fixed
                    }
                )
        return super().to_internal_value(data)


class ExpenseVoidSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=1000, help_text="Why it is voided. Required; kept in the audit log.")
