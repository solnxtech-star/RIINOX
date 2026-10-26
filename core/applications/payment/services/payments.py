from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Final

from django.contrib.contenttypes.models import ContentType
from django.db import transaction

from core.applications.invoice.services.balance import recalculate_customer_balance
from core.applications.invoice.services.purchase_invoices import (
    apply_payment_to_purchase_invoice,
)
from core.applications.payment.exceptions import PaymentValidationError
from core.applications.payment.models import InvoicePayment, Payment, PurchasePayment
from core.applications.transactions.services import (
    TransactionData,
    create_transaction,
    record_payment_financial_entries,
    record_purchase_payment_financial_entries,
)
from core.helper.enums import (
    InvoiceStatusChoices,
    PaymentMethod,
    PaymentStatus,
    PurchasePaymentStatusChoices,
    PurchaseStatusChoices,
    SalePaymentStatusChoices,
    TransactionStatusChoices,
    TransactionTypeChoices,
)
from core.helper.money import quantize_money
from core.helper.utils import generate_reference

if TYPE_CHECKING:
    from core.applications.invoice.models import Invoice
    from core.applications.purchase.models import Purchase

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


@dataclass(frozen=True, slots=True)
class InvoicePaymentInput:
    """
    Parameter object carrying customer invoice payment details.
    Currency defaults to `invoice.currency` when omitted.
    """

    method: str
    amount: Decimal | None = None
    currency: str | None = None
    reference: str | None = None
    description: str = ""


@transaction.atomic
def record_invoice_payment(
    invoice: Invoice,
    *,
    actor: Any,
    payload: InvoicePaymentInput | None = None,
    **legacy_kwargs: Any,
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
    resolved_payload = payload or InvoicePaymentInput(
        method=legacy_kwargs.get("method", ""),
        amount=legacy_kwargs.get("amount"),
        currency=legacy_kwargs.get("currency"),
        reference=legacy_kwargs.get("reference"),
        description=legacy_kwargs.get("description", ""),
    )
    amount = resolved_payload.amount
    method = resolved_payload.method
    currency = resolved_payload.currency or getattr(invoice, "currency", None) or "NGN"
    reference = resolved_payload.reference
    description = resolved_payload.description

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
    recalculate_customer_balance(customer)
    trx = create_transaction(
        TransactionData(
            transaction_id=f"PAY-{str(payment.id)[:8]}-{uuid.uuid4().hex[:6]}".upper(),
            organization=invoice.organization,
            transaction_type=TransactionTypeChoices.PAYMENT_RECEIVED,
            status=TransactionStatusChoices.COMPLETED,
            content_type=ContentType.objects.get_for_model(payment),
            object_id=payment.id,
            created_by=actor,
            notes=description or f"Payment for invoice {invoice.invoice_number}",
        )
    )
    record_payment_financial_entries(payment, trx)

    return RecordPaymentResult(
        payment=payment,
        receipt=receipt,
        change_due=change_due,
    )


class RecordPurchasePaymentResult:
    """Result of recording a supplier payment against a Purchase Order."""

    def __init__(
        self,
        payment: Payment,
        purchase_payment: PurchasePayment,
    ):
        self.payment = payment
        self.purchase_payment = purchase_payment

    def __iter__(self):
        yield self.payment
        yield self.purchase_payment

    def __getitem__(self, index):
        return (self.payment, self.purchase_payment)[index]

    def __repr__(self) -> str:
        return (
            f"RecordPurchasePaymentResult(payment={self.payment}, "
            f"purchase_payment={self.purchase_payment})"
        )


@dataclass(frozen=True, slots=True)
class PurchasePaymentInput:
    """
    Parameter object carrying the supplier payment payload (UI Page 15/20).
    Currency is derived directly from `purchase.organization.currency`.
    """

    method: str
    amount: Decimal | None = None
    reference: str | None = None
    description: str = ""
    payment_date: Any = None
    notes: str = ""
    supporting_document: Any = None


@transaction.atomic
def record_purchase_payment(
    purchase: Purchase,
    *,
    actor: Any,
    payload: PurchasePaymentInput,
) -> RecordPurchasePaymentResult:
    """
    Records a supplier payment against a Purchase Order (PRD §18, UI Page 15):
    - Locks `purchase` under `select_for_update()`.
    - Validates purchase status, payable total (active PurchaseInvoice total or PO total_value), and balance due.
    - Creates `Payment` and `PurchasePayment`.
    - Updates `purchase.amount_paid`, `purchase.payment_status`, and synchronizes `PurchaseInvoice`.
    - Creates `Transaction` (`PAYMENT_SENT`) and double-entry `FinancialLedgerEntry` (`Debit: ACCOUNTS_PAYABLE`, `Credit: CASH_BANK`).
    """
    purchase = purchase.__class__.objects.select_for_update().get(pk=purchase.pk)

    if purchase.status == PurchaseStatusChoices.CANCELLED:
        raise PaymentValidationError(
            "Cannot record a payment against a cancelled purchase order.",
            detail={"purchase_id": str(purchase.pk), "status": str(purchase.status)},
        )

    active_invoice = None
    if hasattr(purchase, "purchase_invoices"):
        active_invoice = (
            purchase.purchase_invoices.exclude(status=InvoiceStatusChoices.CANCELLED)
            .order_by("-created_at")
            .first()
        )

    has_received_goods = purchase.items.filter(quantity_received__gt=0).exists()
    if not has_received_goods and active_invoice is None:
        raise PaymentValidationError(
            "Cannot record a supplier payment before goods are received or a supplier invoice is recorded.",
            detail={"purchase_id": str(purchase.pk), "status": str(purchase.status)},
        )

    payable_total = (
        Decimal(str(active_invoice.total)).quantize(Decimal("0.01"))
        if active_invoice is not None
        else Decimal(str(purchase.total_value)).quantize(Decimal("0.01"))
    )

    current_paid = Decimal(str(purchase.amount_paid or 0)).quantize(Decimal("0.01"))
    if purchase.payment_status == PurchasePaymentStatusChoices.PAID or current_paid >= payable_total:
        raise PaymentValidationError(
            "This purchase order is already fully paid.",
            detail={
                "purchase_id": str(purchase.pk),
                "payment_status": str(purchase.payment_status),
            },
        )

    balance_due = (payable_total - current_paid).quantize(Decimal("0.01"))

    if isinstance(payload.method, PaymentMethod):
        resolved_method = str(payload.method.value)
    else:
        try:
            resolved_method = str(PaymentMethod(str(payload.method).lower()).value)
        except ValueError as exc:
            raise PaymentValidationError(
                "A valid payment method is required.",
                detail={"method": str(payload.method)},
            ) from exc

    if payload.amount is None or payload.amount == "":
        paid_amount = balance_due
    else:
        paid_amount = _money(payload.amount, field="payment amount")

    if paid_amount <= Decimal("0.00"):
        raise PaymentValidationError(
            "A supplier payment must be greater than zero.",
            detail={"amount": str(paid_amount)},
        )

    if paid_amount > balance_due:
        raise PaymentValidationError(
            f"Payment amount {paid_amount} exceeds the remaining balance due {balance_due}.",
            detail={"amount": str(paid_amount), "balance_due": str(balance_due)},
        )

    new_paid = (current_paid + paid_amount).quantize(Decimal("0.01"))
    balance_after = max(Decimal("0.00"), (payable_total - new_paid).quantize(Decimal("0.01")))

    payment_notes = (
        payload.notes or payload.description or f"Supplier payment for purchase {purchase.purchase_number}"
    )
    resolved_currency = getattr(purchase.organization, "currency", None) or "NGN"

    payment = Payment.objects.create(
        user=actor,
        amount=paid_amount,
        currency=resolved_currency,
        method=resolved_method,
        status=PaymentStatus.SUCCESS,
        reference=payload.reference or _reference("PPAY", purchase.purchase_number),
        description=payment_notes,
    )

    purchase.amount_paid = new_paid
    purchase.payment_status = (
        PurchasePaymentStatusChoices.PAID
        if new_paid >= payable_total
        else PurchasePaymentStatusChoices.PARTIALLY_PAID
    )
    purchase.save(update_fields=["amount_paid", "payment_status", "updated_at"])

    apply_payment_to_purchase_invoice(purchase, new_amount_paid=new_paid)

    purchase_payment = PurchasePayment.objects.create(
        purchase=purchase,
        payment=payment,
        recorded_by=actor,
        amount=paid_amount,
        balance_after=balance_after,
        payment_date=payload.payment_date,
        notes=payload.notes or payload.description or None,
        supporting_document=payload.supporting_document,
    )

    transaction_record = create_transaction(
        TransactionData(
            transaction_id=f"PPAY-{str(payment.id)[:8]}-{uuid.uuid4().hex[:6]}".upper(),
            organization=purchase.organization,
            transaction_type=TransactionTypeChoices.PAYMENT_SENT,
            status=TransactionStatusChoices.COMPLETED,
            content_type=ContentType.objects.get_for_model(payment),
            object_id=payment.id,
            created_by=actor,
            notes=payment_notes,
        )
    )
    record_purchase_payment_financial_entries(payment, transaction_record)

    return RecordPurchasePaymentResult(
        payment=payment,
        purchase_payment=purchase_payment,
    )
