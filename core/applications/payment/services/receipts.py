"""
Customer payments received against an invoice (PRD §21, §24-26, §52).

`receive_invoice_payment` is what the API calls. It finds the invoice inside the
caller's organization and locations, makes a client retry safe, then hands the
money work to `record_invoice_payment` (Payment, receipt, invoice status,
customer balance and ledger in one go) and adds the audit event. Everything
commits together or not at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from django.apps import apps
from django.db import IntegrityError, transaction
from django.utils import timezone

from core.applications.invoice.exceptions import InvoiceNotFoundError
from core.applications.invoice.models import Invoice
from core.applications.notification.audit.action import AuditAction
from core.applications.notification.audit.services import record as audit_record
from core.applications.payment.exceptions import DuplicatePaymentReferenceError
from core.applications.payment.models import InvoicePayment, Payment
from core.applications.payment.services.payments import (
    InvoicePaymentInput,
    payment_status,
    record_invoice_payment,
)
from core.applications.subscriptions.services.entitlements import ensure_subscription_active

if TYPE_CHECKING:
    from core.applications.users.models import Organization
    from core.applications.warehouse.access import LocationScope

__all__ = ["ReceivePaymentInput", "ReceivedPayment", "receive_invoice_payment"]


@dataclass(frozen=True, slots=True)
class ReceivePaymentInput:
    invoice_id: Any
    method: str
    amount: Decimal | None = None
    reference: str = ""
    note: str = ""


@dataclass(frozen=True, slots=True)
class ReceivedPayment:
    receipt: InvoicePayment
    change_due: Decimal
    # True when the reference matched a payment already recorded on this invoice:
    # nothing new was written and `receipt` is the original.
    replayed: bool = False


@transaction.atomic
def receive_invoice_payment(
    *,
    organization: Organization,
    actor: Any,
    data: ReceivePaymentInput,
    location_scope: LocationScope | None = None,
    audit_context: Any = None,
) -> ReceivedPayment:
    """
    Record money a customer paid towards one of the organization's invoices.

    - The invoice must belong to `organization` and, with `location_scope`, to one
      of the caller's locations. Otherwise `404 INVOICE_NOT_FOUND`, so other
      tenants' invoices are never revealed.
    - `reference` identifies the payment. Sending a reference already recorded on
      this invoice with the same method returns that payment (a safe retry);
      any other reuse is `409 DUPLICATE_PAYMENT_REFERENCE`.
    - Amount, status and overpayment rules are `record_invoice_payment`'s.
    """
    ensure_subscription_active(organization)

    invoices = Invoice.objects.for_organization(organization)
    if location_scope is not None:
        invoices = location_scope.apply(invoices, "warehouse")
    # Locked first, so two retries of the same payment run one after the other
    # and the second sees the first's receipt.
    invoice = invoices.select_for_update().filter(pk=data.invoice_id).first()
    if invoice is None:
        raise InvoiceNotFoundError(detail={"invoice_id": str(data.invoice_id)})

    reference = (data.reference or "").strip()
    if reference:
        replay = _replay(invoice, reference, data.method)
        if replay is not None:
            return replay

    try:
        with transaction.atomic():
            result = record_invoice_payment(
                invoice,
                actor=actor,
                payload=InvoicePaymentInput(
                    method=data.method,
                    amount=data.amount,
                    reference=reference or None,
                    description=(data.note or "").strip(),
                ),
            )
    except IntegrityError as exc:
        # The reference was taken between the check and the insert (another invoice).
        raise DuplicatePaymentReferenceError(detail={"reference": reference}) from exc

    invoice.refresh_from_db(fields=["amount_paid", "status"])
    _sync_sale_payment_status(invoice)

    receipt = result.receipt
    audit_record(
        action=AuditAction.INVOICE_PAYMENT_RECORDED,
        organization=organization,
        actor=actor,
        resource=receipt,
        previous_values={
            "amount_paid": str(invoice.amount_paid - receipt.amount),
        },
        new_values={
            "amount_paid": str(invoice.amount_paid),
            "status": invoice.status,
        },
        metadata={
            "invoice_number": invoice.invoice_number,
            "receipt_number": receipt.receipt_number,
            "amount": str(receipt.amount),
            "method": result.payment.method,
            "reference": result.payment.reference,
            "balance_after": str(receipt.balance_after),
            "change_due": str(result.change_due),
        },
        context=audit_context,
    )
    return ReceivedPayment(receipt=receipt, change_due=result.change_due)


def _replay(invoice: Invoice, reference: str, method: str) -> ReceivedPayment | None:
    payment = Payment.objects.filter(reference=reference).first()
    if payment is None:
        return None
    receipt = InvoicePayment.objects.filter(payment=payment, invoice=invoice).first()
    if receipt is None or payment.method != str(method):
        raise DuplicatePaymentReferenceError(detail={"reference": reference})
    return ReceivedPayment(receipt=receipt, change_due=Decimal("0.00"), replayed=True)


def _sync_sale_payment_status(invoice: Invoice) -> None:
    """A sale's payment status follows its invoice when the balance is paid later."""
    if not invoice.sale_id:
        return
    sale_model = apps.get_model("sales", "Sale")
    sale_model.objects.filter(pk=invoice.sale_id).update(
        payment_status=payment_status(invoice.total, invoice.amount_paid),
        updated_at=timezone.now(),
    )
