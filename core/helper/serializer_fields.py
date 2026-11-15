"""Reusable DRF serializer fields shared across apps."""

from __future__ import annotations

from rest_framework import serializers

from core.helper.enums import PaymentMethod
from core.helper.enums import UnitOfMeasureChoices

# The only accepted unit names (SaleItem / unit conversions use these choices).
UNIT_NAMES_NOTE = ", ".join(f"`{value}`" for value in UnitOfMeasureChoices.values)

PAYMENT_METHODS_NOTE = (
    "One of: " + ", ".join(f"`{value}`" for value in PaymentMethod.values) + ". Case-insensitive "
    "(`CASH` is accepted and stored as `cash`)."
)


class PaymentMethodField(serializers.ChoiceField):
    """
    A payment method from `PaymentMethod` (PRD §26). Case-insensitive on input,
    always the lowercase value on output, and the schema shows the exact list, so
    the frontend never has to guess which strings are accepted.
    """

    default_error_messages = {
        "invalid_choice": "\"{input}\" is not a valid payment method. " + PAYMENT_METHODS_NOTE,
    }

    def __init__(self, **kwargs):
        kwargs.setdefault("help_text", PAYMENT_METHODS_NOTE)
        super().__init__(choices=PaymentMethod.choices, **kwargs)

    def to_internal_value(self, data):
        if isinstance(data, str):
            data = data.strip().lower()
        return super().to_internal_value(data)
