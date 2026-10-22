from __future__ import annotations

from typing import Any


class PaymentError(Exception):
    """Base class for every business-rule failure in the payment domain."""

    code: str = "PAYMENT_ERROR"
    default_message: str = "The payment operation could not be completed."

    def __init__(self, message: str | None = None, *, detail: dict[str, Any] | None = None) -> None:
        self.message = message or self.default_message
        self.detail = detail or {}
        super().__init__(self.message)


class PaymentValidationError(PaymentError):
    code: str = "PAYMENT_VALIDATION_ERROR"
    default_message = "Payment validation failed."
