from rest_framework import status

from core.helper.custom_exceptions import DomainError


class PurchaseError(DomainError):
    code = "PURCHASE_ERROR"
    default_message = "The purchase operation could not be completed."
    status_code = status.HTTP_400_BAD_REQUEST


class PurchaseValidationError(PurchaseError):
    code = "PURCHASE_VALIDATION_ERROR"
    default_message = "Invalid purchase order data."


class PurchaseReceiveError(PurchaseError):
    code = "RECEIVE_ERROR"
    default_message = "Could not process goods receipt for this purchase order."


class CannotDeleteReceivedPurchaseError(PurchaseError):
    code = "CANNOT_DELETE_RECEIVED_PURCHASE"
    default_message = "Cannot delete a purchase order that has already received goods. Cancel it instead."


class PurchaseCancellationError(PurchaseError):
    code = "PURCHASE_CANCELLATION_ERROR"
    default_message = "This purchase order cannot be cancelled in its current state."


class PurchaseReturnError(PurchaseError):
    code = "PURCHASE_RETURN_ERROR"
    default_message = "Could not process supplier return for this purchase order."

