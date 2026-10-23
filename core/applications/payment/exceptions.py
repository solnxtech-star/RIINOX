from __future__ import annotations

from typing import Any

from rest_framework import status

from core.helper.custom_exceptions import BusinessRuleError


class PaymentError(BusinessRuleError):
    """Base class for every business-rule failure in the payment domain."""

    code: str = "PAYMENT_ERROR"
    default_message: str = "The payment operation could not be completed."

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        status_code: int = status.HTTP_400_BAD_REQUEST,
        detail: dict[str, Any] | None = None,
    ) -> None:
        msg = message or self.default_message
        c = code or self.code
        super().__init__(
            code=c,
            message=msg,
            status_code=status_code,
            extra={"detail": detail} if detail is not None else None,
        )


class PaymentValidationError(PaymentError):
    code: str = "PAYMENT_VALIDATION_ERROR"
    default_message = "Payment validation failed."
