from __future__ import annotations

from rest_framework import status

from core.helper.custom_exceptions import DomainError


class PaymentError(DomainError):
    """Base class for every business-rule failure in the payment domain."""

    code = "PAYMENT_ERROR"
    default_message = "The payment operation could not be completed."


class PaymentValidationError(PaymentError):
    """The payment data itself is wrong: a bad amount, method or date."""

    code = "PAYMENT_VALIDATION_ERROR"
    default_message = "Payment validation failed."
    status_code = status.HTTP_400_BAD_REQUEST


class InvoiceNotPayableError(PaymentError):
    """The invoice's status does not allow money to be recorded against it."""

    code = "INVOICE_NOT_PAYABLE"
    default_message = "This invoice cannot take a payment in its current status."
    status_code = status.HTTP_409_CONFLICT


class PaymentExceedsBalanceError(PaymentError):
    """The amount is more than what is still owed. Overpayment is never recorded."""

    code = "PAYMENT_EXCEEDS_BALANCE"
    default_message = "The payment is more than the invoice balance."
    status_code = status.HTTP_409_CONFLICT
