from __future__ import annotations

import uuid
from decimal import ROUND_HALF_UP
from decimal import Decimal
from decimal import InvalidOperation
from typing import TYPE_CHECKING
from typing import Any
from typing import Final

from django.db import transaction

from core.applications.invoice.services.balance import recalculate_customer_balance
from core.applications.payment.exceptions import PaymentValidationError
from core.applications.payment.models import InvoicePayment
from core.applications.payment.models import Payment
from core.helper.enums import InvoiceStatusChoices
from core.helper.enums import PaymentMethod
from core.helper.enums import PaymentStatus
from core.helper.enums import SalePaymentStatusChoices

if TYPE_CHECKING:
    from core.applications.invoice.models import Invoice

CENT: Final = Decimal("0.01")

# Invoices that can still receive a payment.
PAYABLE_STATUSES: Final = (
    InvoiceStatusChoices.CONFIRMED,
    InvoiceStatusChoices.OVERDUE,
    InvoiceStatusChoices.PARTIALLY_PAID,
)


def _money(value: Any, *, field: str = "amount") -> Decimal:
    try:
        return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError) as exc:
        msg = f"Invalid {field}."
        raise PaymentValidationError(msg, detail={field: str(value)}) from exc


def _reference(prefix: str, key: str) -> str:
    return f"{prefix}-{key}-{uuid.uuid4().hex[:6].upper()}"



def parse_payment(payment: dict[str, Any] | None, total: Decimal) -> tuple[Decimal, str, str | None]:
    """
    Parse and validate payment tendered with a sale (PRD §39: {"amount", "method", "reference"}).
    Returns (amount, method, caller reference). Overpayment is refused: change
    is given at the till, it is not recorded as received.
    """
    if not payment:
        return Decimal("0.00"), "", None
    amount = _money(payment.get("amount") or 0, field="payment amount")
    if not Decimal(0) <= amount <= total:
        msg = "The payment must be between zero and the sale total."
        raise PaymentValidationError(msg, detail={"amount": str(amount), "total": str(total)})
    if amount == 0:
        return amount, "", None
    raw_method = payment.get("method")
    if isinstance(raw_method, PaymentMethod):
        method = raw_method
    else:
        try:
            method = PaymentMethod(str(raw_method).lower() if raw_method is not None else "")
        except ValueError as exc:
            msg = "A valid payment method is required."
            raise PaymentValidationError(msg, detail={"method": str(raw_method)}) from exc
    return amount, str(method.value), payment.get("reference") or None


def payment_status(total: Decimal, paid: Decimal) -> str:
    """Determine Sale payment status based on total and paid amounts."""
    if paid >= total:
        return SalePaymentStatusChoices.PAID
    if paid > 0:
        return SalePaymentStatusChoices.PARTIALLY_PAID
    return SalePaymentStatusChoices.UNPAID


@transaction.atomic
def record_invoice_payment(
    invoice: Invoice,
    *,
    amount: Decimal,
    method: str,
    actor: Any,
    currency: str,
    reference: str | None = None,
    description: str = "",
) -> tuple[Payment, InvoicePayment]:
    """
    Record money received against an invoice: the Payment, the receipt
    (InvoicePayment), the invoice's `amount_paid` and status, and the customer's
    cached balance, all together. This is the only place `amount_paid` is
    written, so the payment records stay the source of truth.

    Call inside the caller's transaction. The invoice row is locked and the
    customer row is locked after it (invoice -> customer).
    """
    amount = _money(amount, field="payment amount")
    if amount <= 0:
        msg = "A payment must be greater than zero."
        raise PaymentValidationError(msg, detail={"amount": str(amount)})

    invoice = invoice.__class__.objects.select_for_update().get(pk=invoice.pk)
    if invoice.status not in PAYABLE_STATUSES:
        msg = f"An invoice that is {invoice.status} cannot take a payment."
        raise PaymentValidationError(msg, detail={"invoice_id": str(invoice.pk)})
    new_paid = invoice.amount_paid + amount
    if new_paid > invoice.total:
        msg = "The payment exceeds the invoice balance."
        raise PaymentValidationError(
            msg,
            detail={"amount": str(amount), "balance_due": str(invoice.balance_due)},
        )

    customer = invoice.customer.__class__.objects.select_for_update().get(pk=invoice.customer_id)

    payment = Payment.objects.create(
        user=actor,
        amount=amount,
        currency=currency,
        method=method,
        status=PaymentStatus.SUCCESS,
        reference=reference or _reference("PAY", invoice.invoice_number),
        description=description or f"Payment for invoice {invoice.invoice_number}",
    )

    invoice.amount_paid = new_paid
    invoice.status = (
        InvoiceStatusChoices.PAID if new_paid >= invoice.total else InvoiceStatusChoices.PARTIALLY_PAID
    )
    invoice.save(update_fields=["amount_paid", "status", "updated_at"])

    receipt = InvoicePayment.objects.create(
        invoice=invoice,
        payment=payment,
        sales_rep=actor,
        amount=amount,
        balance_after=invoice.total - new_paid,
    )
    recalculate_customer_balance(customer)
    return payment, receipt
