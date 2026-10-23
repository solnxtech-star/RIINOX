from __future__ import annotations

from typing import Any

from rest_framework import status

from core.applications.invoice.exceptions import InvoiceValidationError
from core.helper.custom_exceptions import BusinessRuleError


class SaleError(BusinessRuleError):
    code: str = "SALE_ERROR"
    default_message: str = "The sale operation could not be completed."

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
        self.detail = detail or {}


class SaleValidationError(SaleError, InvoiceValidationError):
    """
    A sale or return request that breaks a business rule. Shares the invoice
    error contract so the API layer maps it to the same structured response.
    """
    code: str = "SALE_VALIDATION_ERROR"
    default_message = "Sale validation failed."


class SaleNotFoundError(SaleError):
    code: str = "SALE_NOT_FOUND"
    default_message = "Sale not found."

    def __init__(
        self,
        message: str | None = None,
        *,
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message=message or self.default_message,
            code=self.code,
            status_code=status.HTTP_404_NOT_FOUND,
            detail=detail,
        )


class SaleCancellationError(SaleError):
    code: str = "SALE_CANCELLATION_ERROR"
    default_message = "This sale cannot be cancelled."
