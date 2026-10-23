from __future__ import annotations

from typing import Any

from rest_framework import status

from core.helper.custom_exceptions import BusinessRuleError


class DocumentError(BusinessRuleError):
    """Base class for every business-rule failure in the document domain."""

    code: str = "DOCUMENT_ERROR"
    default_message: str = "The document operation could not be completed."

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


class DefaultTemplateMissingError(DocumentError):
    code = "DEFAULT_TEMPLATE_MISSING"
    default_message = "A required shared default template has not been seeded."


class FeatureNotAvailableError(DocumentError):
    code = "FEATURE_NOT_AVAILABLE"
    default_message = "This feature is not included in the organization's current plan."


class InvalidDocumentTypeError(DocumentError):
    code = "INVALID_DOCUMENT_TYPE"
    default_message = "Unknown document type."


class TemplateNotAvailableError(DocumentError):
    code = "TEMPLATE_NOT_AVAILABLE"
    default_message = "This template cannot be used by the organization."


class InvalidTemplateFileError(DocumentError):
    code = "INVALID_TEMPLATE_FILE"
    default_message = "The uploaded template file is not valid."


class DuplicateTemplateNameError(DocumentError):
    code = "DUPLICATE_TEMPLATE_NAME"
    default_message = "The organization already has a template with this name."


class InvalidSequenceSettingError(DocumentError):
    code = "INVALID_SEQUENCE_SETTING"
    default_message = "The numbering settings are not valid."


class InvoiceNotFoundError(DocumentError):
    """Also raised for another tenant's invoice, so existence is never leaked."""

    code = "INVOICE_NOT_FOUND"
    default_message = "Invoice not found."


class InvoiceValidationError(DocumentError):
    code = "INVOICE_VALIDATION_ERROR"
    default_message = "The invoice data is not valid."


class InvalidInvoiceTransitionError(DocumentError):
    code = "INVALID_INVOICE_TRANSITION"
    default_message = "The invoice cannot move to that status from its current status."


class InsufficientStockError(DocumentError):
    """
    Part of the standalone-invoice stock contract. The adapter translates the
    inventory app's own typed error into this one.
    """

    code = "INSUFFICIENT_STOCK"
    default_message = "There is not enough stock to confirm this sale."


class InvoiceHasPaymentsError(DocumentError):
    code = "INVOICE_HAS_PAYMENTS"
    default_message = "An invoice with payments cannot be voided. Reverse the payments first."


class InvoiceAlreadyIssuedForSaleError(DocumentError):
    code = "INVOICE_ALREADY_ISSUED_FOR_SALE"
    default_message = "A live invoice has already been issued for this sale."


class ApprovalRequiredError(DocumentError):
    code = "APPROVAL_REQUIRED"
    default_message = "This action requires approval from a user with the appropriate permissions."


class CreditLimitExceededError(DocumentError):
    code = "CREDIT_LIMIT_EXCEEDED"
    default_message = "This sale would take the customer over their credit limit and needs an approved override."
