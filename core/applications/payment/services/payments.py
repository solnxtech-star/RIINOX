from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any, Final

from django.db import transaction
from django.db.models import F, Sum

from core.applications.payment.exceptions import PaymentValidationError
from core.applications.payment.models import InvoicePayment, Payment
from core.helper.money import CENT, quantize_money
from core.helper.utils import generate_reference

if TYPE_CHECKING:
    from core.applications.customers.models import Customer
    from core.applications.invoice.models import Invoice
from core.helper.enums import (
    InvoiceStatusChoices,
    PaymentMethod,
    PaymentStatus,
    SalePaymentStatusChoices,
)

# Invoices that can still receive a payment.
PAYABLE_STATUSES: Final = (
    InvoiceStatusChoices.CONFIRMED,
    InvoiceStatusChoices.OVERDUE,
    InvoiceStatusChoices.PARTIALLY_PAID,
)


def _money(value: Any, *, field: str = "amount") -> Decimal:
    try:
        return quantize_money(value, field=field)
    except ValueError as exc:
        msg = f"Invalid {field}."
        raise PaymentValidationError(msg, detail={field: str(value)}) from exc


_reference = generate_reference



def _sync_customer_balance(customer: Any) -> None:
    """
    Recompute Customer.balance as total - amount_paid over the customer's
    Confirmed, Overdue and Partially Paid invoices.
    """
    outstanding_statuses = (
        InvoiceStatusChoices.CONFIRMED,
        InvoiceStatusChoices.OVERDUE,
        InvoiceStatusChoices.PARTIALLY_PAID,
    )
    owed = (
        customer.invoices.filter(status__in=outstanding_statuses).aggregate(
            owed=Sum(F("total") - F("amount_paid"))
        )["owed"]
        or Decimal("0.00")
    )
    customer.__class__.objects.filter(pk=customer.pk).update(balance=owed)
    customer.balance = owed


class PaymentParseResult:
    """Result of parsing and validating payment tendered with a sale."""

    def __init__(
        self,
        amount: Decimal,
        method: str,
        reference: str | None,
        change_due: Decimal = Decimal("0.00"),
    ):
        self.amount = amount
        self.method = method
        self.reference = reference
        self.change_due = change_due

    def __iter__(self):
        yield self.amount
        yield self.method
        yield self.reference

    def __getitem__(self, index):
        return (self.amount, self.method, self.reference, self.change_due)[index]

    def __repr__(self) -> str:
        return (
            f"PaymentParseResult(amount={self.amount}, change_due={self.change_due}, "
            f"method='{self.method}', reference={self.reference!r})"
        )


def parse_payment(payment: dict[str, Any] | None, total: Decimal) -> PaymentParseResult:
    """
    Parse and validate payment tendered with a sale (PRD §39: {"amount", "method", "reference"}).

    Rules:
    - Amount omitted: automatically defaults to the full outstanding balance (`total`).
    - Cash payment > total: records `total` as received and calculates `change_due = amount - total`.
    - Partial payment (< total): records partial amount received (`change_due = 0.00`).
    - Card/non-cash payment > total: strictly rejected with PaymentValidationError.
    """
    if not payment:
        return PaymentParseResult(Decimal("0.00"), "", None, Decimal("0.00"))

    raw_method = payment.get("method")
    if isinstance(raw_method, PaymentMethod):
        method = raw_method
    else:
        try:
            method = PaymentMethod(str(raw_method).lower() if raw_method is not None else "")
        except ValueError as exc:
            msg = "A valid payment method is required."
            raise PaymentValidationError(msg, detail={"method": str(raw_method)}) from exc

    raw_amount = payment.get("amount")
    if raw_amount is None or raw_amount == "":
        amount = total
    else:
        amount = _money(raw_amount, field="payment amount")

    if amount < Decimal("0.00"):
        msg = "The payment amount cannot be negative."
        raise PaymentValidationError(msg, detail={"amount": str(amount)})

    if amount == Decimal("0.00"):
        return PaymentParseResult(
            Decimal("0.00"), str(method.value), payment.get("reference") or None, Decimal("0.00")
        )

    norm_method = str(method.value).lower()
    change_due = Decimal("0.00")
    if amount > total:
        if norm_method == "cash":
            change_due = amount - total
            paid_now = total
        else:
            msg = f"Overpayment is not allowed for {method.value} payments. Tendered {amount} exceeds sale total {total}."
            raise PaymentValidationError(
                msg,
                detail={"amount": str(amount), "total": str(total), "method": str(method.value)},
            )
    else:
        paid_now = amount

    return PaymentParseResult(paid_now, str(method.value), payment.get("reference") or None, change_due)


def payment_status(total: Decimal, paid: Decimal) -> str:
    """Determine Sale payment status based on total and paid amounts."""
    if paid >= total:
        return SalePaymentStatusChoices.PAID
    if paid > 0:
        return SalePaymentStatusChoices.PARTIALLY_PAID
    return SalePaymentStatusChoices.UNPAID


class RecordPaymentResult:
    """Result of recording a payment against an invoice."""

    def __init__(
        self,
        payment: Payment,
        receipt: InvoicePayment,
        change_due: Decimal = Decimal("0.00"),
    ):
        self.payment = payment
        self.receipt = receipt
        self.change_due = change_due

    def __iter__(self):
        yield self.payment
        yield self.receipt

    def __getitem__(self, index):
        return (self.payment, self.receipt, self.change_due)[index]

    def __repr__(self) -> str:
        return (
            f"RecordPaymentResult(payment={self.payment}, receipt={self.receipt}, "
            f"change_due={self.change_due})"
        )


@transaction.atomic
def record_invoice_payment(
    invoice: Invoice,
    *,
    amount: Decimal | None = None,
    method: str,
    actor: Any,
    currency: str,
    reference: str | None = None,
    description: str = "",
) -> RecordPaymentResult:
    """
    Record money received against an invoice: the Payment, the receipt
    (InvoicePayment), the invoice's `amount_paid` and status, and the customer's
    cached balance, all together. This is the only place `amount_paid` is
    written, so the payment records stay the source of truth.

    Rules:
    - If invoice is already fully paid: raises PaymentValidationError.
    - If amount is omitted (None): defaults to the remaining invoice balance (`total - amount_paid`).
    - If cash payment > balance_due: records balance_due received and calculates change_due.
    - If card/non-cash payment > balance_due: rejected with PaymentValidationError.
    """
    invoice = invoice.__class__.objects.select_for_update().get(pk=invoice.pk)
    if invoice.status == InvoiceStatusChoices.PAID or invoice.amount_paid >= invoice.total:
        msg = "The invoice is already fully paid."
        raise PaymentValidationError(
            msg, detail={"invoice_id": str(invoice.pk), "status": str(invoice.status)}
        )

    if invoice.status not in PAYABLE_STATUSES:
        msg = f"An invoice that is {invoice.status} cannot take a payment."
        raise PaymentValidationError(msg, detail={"invoice_id": str(invoice.pk)})

    balance_due = invoice.total - invoice.amount_paid

    if amount is None or amount == "":
        amount_val = balance_due
    else:
        amount_val = _money(amount, field="payment amount")

    if amount_val <= Decimal("0.00"):
        msg = "A payment must be greater than zero."
        raise PaymentValidationError(msg, detail={"amount": str(amount_val)})

    norm_method = str(method).lower()
    change_due = Decimal("0.00")
    if amount_val > balance_due:
        if norm_method == "cash":
            change_due = amount_val - balance_due
            paid_amount = balance_due
        else:
            msg = f"Overpayment is not allowed for {method} payments. Tendered {amount_val} exceeds balance due {balance_due}."
            raise PaymentValidationError(
                msg,
                detail={"amount": str(amount_val), "balance_due": str(balance_due), "method": str(method)},
            )
    else:
        paid_amount = amount_val

    new_paid = invoice.amount_paid + paid_amount

    customer = invoice.customer.__class__.objects.select_for_update().get(pk=invoice.customer_id)

    payment = Payment.objects.create(
        user=actor,
        amount=paid_amount,
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
        amount=paid_amount,
        balance_after=invoice.total - new_paid,
    )
    _sync_customer_balance(customer)
    return RecordPaymentResult(payment, receipt, change_due)
