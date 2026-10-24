from __future__ import annotations

from rest_framework import status

from core.helper.custom_exceptions import DomainError


class DocumentError(DomainError):
    """Base class for every business-rule failure in the document domain."""

    code = "DOCUMENT_ERROR"
    default_message = "The document operation could not be completed."


# --------------------------------------------------------------------------- #
# Templates, plans and numbering
# --------------------------------------------------------------------------- #


class DefaultTemplateMissingError(DocumentError):
    """A server-side setup problem, not something the caller can fix."""

    code = "DEFAULT_TEMPLATE_MISSING"
    default_message = "A required shared default template has not been seeded."
    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR


class FeatureNotAvailableError(DocumentError):
    code = "FEATURE_NOT_AVAILABLE"
    default_message = "This feature is not included in the organization's current plan."
    status_code = status.HTTP_403_FORBIDDEN


class InvalidDocumentTypeError(DocumentError):
    code = "INVALID_DOCUMENT_TYPE"
    default_message = "Unknown document type."
    status_code = status.HTTP_400_BAD_REQUEST


class TemplateNotAvailableError(DocumentError):
    code = "TEMPLATE_NOT_AVAILABLE"
    default_message = "This template cannot be used by the organization."
    status_code = status.HTTP_403_FORBIDDEN


class InvalidTemplateFileError(DocumentError):
    code = "INVALID_TEMPLATE_FILE"
    default_message = "The uploaded template file is not valid."
    status_code = status.HTTP_400_BAD_REQUEST


class DuplicateTemplateNameError(DocumentError):
    code = "DUPLICATE_TEMPLATE_NAME"
    default_message = "The organization already has a template with this name."
    status_code = status.HTTP_409_CONFLICT


class InvalidSequenceSettingError(DocumentError):
    code = "INVALID_SEQUENCE_SETTING"
    default_message = "The numbering settings are not valid."
    status_code = status.HTTP_400_BAD_REQUEST


# --------------------------------------------------------------------------- #
# Invoices
# --------------------------------------------------------------------------- #


class InvoiceNotFoundError(DocumentError):
    """Also raised for another tenant's invoice, so existence is never leaked."""

    code = "INVOICE_NOT_FOUND"
    default_message = "Invoice not found."
    status_code = status.HTTP_404_NOT_FOUND


class InvoiceValidationError(DocumentError):
    code = "INVOICE_VALIDATION_ERROR"
    default_message = "The invoice data is not valid."
    status_code = status.HTTP_400_BAD_REQUEST


class InvalidInvoiceTransitionError(DocumentError):
    code = "INVALID_INVOICE_TRANSITION"
    default_message = "The invoice cannot move to that status from its current status."
    status_code = status.HTTP_409_CONFLICT


class InsufficientStockError(DocumentError):
    """
    Part of the standalone-invoice stock contract. The adapter translates the
    inventory app's own typed error into this one.
    """

    code = "INSUFFICIENT_STOCK"
    default_message = "There is not enough stock to confirm this sale."
    status_code = status.HTTP_409_CONFLICT


class InvoiceHasPaymentsError(DocumentError):
    code = "INVOICE_HAS_PAYMENTS"
    default_message = "An invoice with payments cannot be voided. Reverse the payments first."
    status_code = status.HTTP_409_CONFLICT


class InvoiceAlreadyIssuedForSaleError(DocumentError):
    code = "INVOICE_ALREADY_ISSUED_FOR_SALE"
    default_message = "A live invoice has already been issued for this sale."
    status_code = status.HTTP_409_CONFLICT


class ApprovalRequiredError(DocumentError):
    code = "APPROVAL_REQUIRED"
    default_message = "This action requires approval from a user with the appropriate permissions."
    status_code = status.HTTP_403_FORBIDDEN


class CreditLimitExceededError(DocumentError):
    code = "CREDIT_LIMIT_EXCEEDED"
    default_message = (
        "This sale would take the customer over their credit limit and needs an approved override."
    )
    status_code = status.HTTP_409_CONFLICT
