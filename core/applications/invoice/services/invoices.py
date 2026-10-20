from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from decimal import ROUND_HALF_UP
from decimal import Decimal
from decimal import InvalidOperation
from functools import partial
from typing import TYPE_CHECKING
from typing import Any
from typing import Final

from django.apps import apps
from django.core.exceptions import ObjectDoesNotExist
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from core.applications.invoice.exceptions import ApprovalRequiredError
from core.applications.invoice.exceptions import CreditLimitExceededError
from core.applications.invoice.exceptions import InvalidInvoiceTransitionError
from core.applications.invoice.exceptions import InvoiceHasPaymentsError
from core.applications.invoice.exceptions import InvoiceNotFoundError
from core.applications.invoice.exceptions import InvoiceValidationError
from core.applications.invoice.models import Client
from core.applications.invoice.models import Invoice
from core.applications.invoice.models import InvoiceItem
from core.applications.invoice.services.documents import allocate_document_number
from core.applications.invoice.services.ports import APPROVAL_CREDIT_OVERRIDE
from core.applications.invoice.services.ports import APPROVAL_INVOICE_VOID
from core.applications.invoice.services.ports import EVENT_INVOICE_CONFIRMED
from core.applications.invoice.services.ports import EVENT_INVOICE_VOIDED
from core.applications.invoice.services.ports import PERM_AUDIT_VIEW
from core.applications.invoice.services.ports import PERM_INVOICE_CONFIRM
from core.applications.invoice.services.ports import PERM_INVOICE_CREATE
from core.applications.invoice.services.ports import PERM_INVOICE_VIEW
from core.applications.invoice.services.ports import PERM_INVOICE_VOID
from core.applications.invoice.services.ports import PricedLine
from core.applications.invoice.services.ports import StockLine
from core.applications.invoice.services.ports import get_invoice_ports
from core.applications.notification.audit.action import AuditAction
from core.applications.notification.audit.services import record
from core.applications.notification.models import AuditLog
from core.helper.enums import TEMPLATE_TYPES
from core.helper.enums import CustomerStatusChoices
from core.helper.enums import InvoiceStatusChoices

if TYPE_CHECKING:
    from collections.abc import Collection
    from collections.abc import Sequence
    from datetime import date
    from datetime import datetime

    from core.applications.notification.audit.context import AuditContext
    from core.applications.users.models import Organization

logger = logging.getLogger(__name__)

_S = InvoiceStatusChoices

# --------------------------------------------------------------------------- #
# Limits and status rules
# --------------------------------------------------------------------------- #

CENT: Final = Decimal("0.01")
# DecimalField(max_digits=12, decimal_places=2) holds at most 10 integer digits.
MAX_MONEY: Final = Decimal("9999999999.99")
MAX_INVOICE_LINES: Final = 500
MAX_LINE_QUANTITY: Final = 1_000_000
MIN_VOID_REASON_LENGTH: Final = 5
DEFAULT_PAYMENT_TERMS_DAYS: Final = 30
OVERDUE_BATCH_SIZE: Final = 1000
MAX_PAGE_SIZE: Final = 200

CONFIRMABLE_STATUSES: Final = (_S.DRAFT, _S.PENDING)
# Confirmed invoices with no payments against them: the only ones a void can reverse.
VOIDABLE_CONFIRMED_STATUSES: Final = (_S.CONFIRMED, _S.OVERDUE)
VOIDABLE_STATUSES: Final = (_S.DRAFT, _S.PENDING, *VOIDABLE_CONFIRMED_STATUSES)
PAID_STATUSES: Final = (_S.PARTIALLY_PAID, _S.PAID)

# Actions an invoice can offer, as shown on the detail page (PRD §38).
ACTION_CONFIRM: Final = "confirm"
ACTION_VOID: Final = "void"
ACTION_VOID_AND_CORRECT: Final = "void_and_correct"

# Raised by Django while converting a malformed id (e.g. "abc" for a UUID or
# integer key) before any SQL runs. Treated as "not found".
_BAD_ID_ERRORS: Final = (ValidationError, ValueError, TypeError)


# --------------------------------------------------------------------------- #
# Inputs
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class InvoiceLineInput:
    """What a caller sends per line. Prices are never accepted from the client."""

    product_id: Any
    quantity: int
    description: str = ""


@dataclass(frozen=True, slots=True)
class InvoiceFilters:
    statuses: tuple[str, ...] = ()
    client_id: Any = None
    sales_rep_id: Any = None
    search: str = ""
    issued_from: date | None = None
    issued_to: date | None = None
    due_from: date | None = None
    due_to: date | None = None
    overdue_only: bool = False
    warehouse_id: Any = None


@dataclass(frozen=True, slots=True)
class _PricedItem:
    product_id: Any
    quantity: int
    unit_price: Decimal
    description: str


@dataclass(frozen=True, slots=True)
class _Totals:
    subtotal: Decimal
    discount: Decimal
    tax: Decimal
    total: Decimal


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _money(value: Decimal | int | str) -> Decimal:
    try:
        return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError) as exc:
        msg = "Invalid money amount."
        raise InvoiceValidationError(msg, detail={"value": str(value)}) from exc


def _model(app_label: str, name: str):
    # Resolved lazily so this module needs no hard import of the products and
    # warehouse apps.
    return apps.get_model(app_label, name)


def _get_or_none(queryset, **lookup):
    """First match, or None. A malformed identifier counts as not found."""
    try:
        return queryset.filter(**lookup).first()
    except _BAD_ID_ERRORS:
        return None


def _snapshot(invoice: Invoice) -> dict[str, Any]:
    """JSON-safe state for audit before/after values."""
    return {
        "invoice_number": invoice.invoice_number,
        "status": str(invoice.status),
        "subtotal": str(invoice.subtotal),
        "discount_amount": str(invoice.discount_amount),
        "tax_amount": str(invoice.tax_amount),
        "total": str(invoice.total),
        "issued_at": invoice.issued_at.isoformat() if invoice.issued_at else None,
        "replaces_id": str(invoice.replaces_id) if invoice.replaces_id else None,
    }


def _load_client(organization: Organization, client_id: Any) -> Client:
    client = _get_or_none(Client.objects.for_organization(organization), pk=client_id)
    if client is None:
        msg = "Client not found."
        raise InvoiceValidationError(msg, detail={"client_id": str(client_id)})
    if not client.is_active or client.status != CustomerStatusChoices.ACTIVE:
        msg = "This client is not active."
        raise InvoiceValidationError(msg, detail={"client_id": str(client_id)})
    return client


def _load_warehouse(organization: Organization, warehouse_id: Any):
    warehouse = _get_or_none(
        _model("warehouse", "Warehouse").objects.filter(organization=organization),
        pk=warehouse_id,
    )
    if warehouse is None:
        msg = "Warehouse not found."
        raise InvoiceValidationError(msg, detail={"warehouse_id": str(warehouse_id)})
    return warehouse


def _resolve_due_date(due_date: date | None) -> date:
    today = timezone.localdate()
    if due_date is None:
        return today + timedelta(days=DEFAULT_PAYMENT_TERMS_DAYS)
    if due_date < today:
        msg = "The due date cannot be in the past."
        raise InvoiceValidationError(msg, detail={"due_date": due_date.isoformat()})
    return due_date


def _validate_lines(lines: Sequence[InvoiceLineInput]) -> set[str]:
    """Shape checks that need no database. Returns the product ids as strings."""
    if not lines:
        msg = "An invoice needs at least one line."
        raise InvoiceValidationError(msg, detail={"lines": []})
    if len(lines) > MAX_INVOICE_LINES:
        msg = f"An invoice can have at most {MAX_INVOICE_LINES} lines."
        raise InvoiceValidationError(msg, detail={"lines": len(lines)})

    seen: set[str] = set()
    for line in lines:
        key = str(line.product_id)
        if key in seen:
            msg = "A product can appear only once per invoice."
            raise InvoiceValidationError(msg, detail={"product_id": key})
        seen.add(key)

        quantity = line.quantity
        is_whole_number = isinstance(quantity, int) and not isinstance(quantity, bool)
        if not is_whole_number or not 1 <= quantity <= MAX_LINE_QUANTITY:
            msg = f"Quantity must be a whole number between 1 and {MAX_LINE_QUANTITY}."
            raise InvoiceValidationError(
                msg,
                detail={"product_id": key, "quantity": str(quantity)},
            )
    return seen


def _price_lines(
    organization: Organization,
    lines: Sequence[InvoiceLineInput],
) -> list[_PricedItem]:
    """Validate lines and price them from the organization's own products in one query."""
    seen = _validate_lines(lines)

    try:
        products = {
            str(p.pk): p
            for p in _model("products", "Product")
            .objects.filter(
                organization=organization,
                pk__in=[line.product_id for line in lines],
            )
            .only("pk", "customer_sale_price")
        }
    except _BAD_ID_ERRORS as exc:
        msg = "One or more product ids are malformed."
        raise InvoiceValidationError(msg, detail={"product_ids": sorted(seen)}) from exc

    missing = sorted(seen - products.keys())
    if missing:
        msg = "Some products were not found."
        raise InvoiceValidationError(msg, detail={"product_ids": missing})

    priced: list[_PricedItem] = []
    for line in lines:
        product = products[str(line.product_id)]
        price = product.customer_sale_price
        if price is None or price < 0:
            msg = "Product has no valid sale price."
            raise InvoiceValidationError(msg, detail={"product_id": str(product.pk)})
        priced.append(
            _PricedItem(
                product_id=product.pk,
                quantity=line.quantity,
                unit_price=_money(price),
                description=(line.description or "").strip()[:255],
            )
        )
    return priced


def _calculate_totals(
    organization: Organization,
    actor: Any,
    items: Sequence[_PricedItem],
    discount_amount: Decimal | int | str,
    discount_approval_id: Any = None,
) -> _Totals:
    ports = get_invoice_ports()
    subtotal = _money(sum((i.unit_price * i.quantity for i in items), Decimal(0)))
    if subtotal > MAX_MONEY:
        # The subtotal column is as narrow as the total: a huge subtotal fully
        # covered by a discount would otherwise pass the total check below and
        # fail at the database instead of as a validation error.
        msg = "The invoice subtotal is too large."
        raise InvoiceValidationError(msg, detail={"subtotal": str(subtotal)})
    discount = _money(discount_amount)
    if discount < 0:
        msg = "The discount cannot be negative."
        raise InvoiceValidationError(msg, detail={"discount": str(discount)})
    if discount > subtotal:
        msg = "The discount cannot exceed the subtotal."
        raise InvoiceValidationError(
            msg,
            detail={"subtotal": str(subtotal), "discount": str(discount)},
        )
    if discount:
        ports.validate_discount(
            actor=actor,
            organization=organization,
            subtotal=subtotal,
            discount=discount,
            approval_id=discount_approval_id,
        )

    taxable = subtotal - discount
    tax = _money(
        ports.calculate_tax(
            organization=organization,
            lines=[PricedLine(i.product_id, i.quantity, i.unit_price) for i in items],
            taxable_amount=taxable,
        )
    )
    if tax < 0:
        msg = "Tax cannot be negative."
        raise InvoiceValidationError(msg, detail={"tax": str(tax)})
    total = taxable + tax
    if total > MAX_MONEY:
        msg = "The invoice total is too large."
        raise InvoiceValidationError(msg, detail={"total": str(total)})
    return _Totals(subtotal=subtotal, discount=discount, tax=tax, total=total)


def _lock_invoice(organization: Organization, invoice_id: Any) -> Invoice:
    """
    Fetch the invoice scoped to the tenant with a row lock on the invoice only.
    A cross-tenant id is indistinguishable from a missing one.
    """
    try:
        return (
            Invoice.objects.for_organization(organization)
            .select_related("client", "warehouse")
            .select_for_update(of=("self",))
            .get(pk=invoice_id)
        )
    except (ObjectDoesNotExist, *_BAD_ID_ERRORS) as exc:
        raise InvoiceNotFoundError(detail={"invoice_id": str(invoice_id)}) from exc


def _require_status(invoice: Invoice, allowed: Sequence[str], action: str) -> None:
    if invoice.status not in allowed:
        msg = f"An invoice that is {invoice.status} cannot be {action}."
        raise InvalidInvoiceTransitionError(
            msg,
            detail={
                "status": str(invoice.status),
                "action": action,
                "invoice_id": str(invoice.pk),
            },
        )


def _stock_lines(invoice: Invoice) -> list[StockLine]:
    """
    Quantities per product, sorted by product id so inventory locks are taken
    in a stable order.
    """
    rows = (
        InvoiceItem.objects.for_invoice(invoice)
        .order_by("product_id")
        .values_list("product_id", "quantity")
    )
    return [StockLine(product_id=pid, quantity=quantity) for pid, quantity in rows]


def _lock_client(invoice: Invoice) -> Client:
    """
    Lock the invoice's client row. Lock order everywhere is
    invoice -> client -> inventory, so concurrent confirms and voids for the
    same client can never deadlock.
    """
    return Client.objects.select_for_update().get(pk=invoice.client_id)


def _refresh_client_balance(client: Client) -> None:
    """
    Recompute the client's cached balance. Call with a client already locked
    via `_lock_client`.

    Without the lock, two concurrent confirmations for the same client each
    recompute from data that excludes the other's uncommitted invoice, and the
    last writer leaves the balance short. The lock only serializes invoices of
    one client; other clients and tenants are unaffected.
    """
    get_invoice_ports().refresh_client_balance(client)


def _enforce_credit_limit(
    organization: Organization,
    actor: Any,
    invoice: Invoice,
    client: Client,
    invoice_total: Decimal,
    upfront_payment: Decimal | int | str,
    approval_id: Any,
) -> bool:
    """
    Block a sale that takes the client over their credit limit unless the
    approval port allows it. A limit of 0 means credit is not tracked for this
    client. Exposure counts only the unpaid part of this invoice.

    Returns True when the sale exceeded the limit and went through on an
    override, so the caller records the approval only when it was relied on.
    """
    paid_now = _money(upfront_payment)
    if not Decimal(0) <= paid_now <= invoice_total:
        msg = "The upfront payment must be between zero and the invoice total."
        raise InvoiceValidationError(
            msg,
            detail={"upfront_payment": str(paid_now), "total": str(invoice_total)},
        )
    if client.credit_limit <= 0:
        return False

    exposure = client.current_balance + invoice_total - paid_now
    if exposure <= client.credit_limit:
        return False

    try:
        get_invoice_ports().require_approval(
            actor=actor,
            organization=organization,
            action=APPROVAL_CREDIT_OVERRIDE,
            resource=invoice,
            approval_id=approval_id,
        )
    except ApprovalRequiredError as exc:
        msg = "This sale would take the client over their credit limit."
        raise CreditLimitExceededError(
            msg,
            detail={
                "credit_limit": str(client.credit_limit),
                "current_balance": str(client.current_balance),
                "invoice_total": str(invoice_total),
                "upfront_payment": str(paid_now),
                "exposure": str(exposure),
            },
        ) from exc
    return True


# --------------------------------------------------------------------------- #
# Create
# --------------------------------------------------------------------------- #


@transaction.atomic
def create_invoice(
    organization: Organization,
    *,
    actor: Any,
    client_id: Any,
    warehouse_id: Any,
    lines: Sequence[InvoiceLineInput],
    due_date: date | None = None,
    discount_amount: Decimal | int | str = 0,
    discount_approval_id: Any = None,
    notes: str | None = "",
    extra_data: dict[str, Any] | None = None,
    replaces: Invoice | None = None,
    audit_context: AuditContext | None = None,
) -> Invoice:
    """
    Create a Draft invoice. No stock moves yet: stock is validated and
    deducted at confirmation (PRD §16, §22).

    The invoice number is allocated here, inside the same transaction, so a
    failed creation never burns a number. Unit prices are snapshotted from the
    product now and are never re-priced.
    """
    ports = get_invoice_ports()
    ports.authorize(actor, organization, PERM_INVOICE_CREATE)

    if replaces is not None and replaces.organization_id != organization.pk:
        msg = "The replaced invoice belongs to another organization."
        raise InvoiceValidationError(msg, detail={"replaces": str(replaces.pk)})

    client = _load_client(organization, client_id)
    warehouse = _load_warehouse(organization, warehouse_id)
    due = _resolve_due_date(due_date)
    items = _price_lines(organization, lines)
    totals = _calculate_totals(
        organization,
        actor,
        items,
        discount_amount,
        discount_approval_id,
    )
    number = allocate_document_number(organization, TEMPLATE_TYPES.INVOICE)

    invoice = Invoice.objects.create(
        organization=organization,
        client=client,
        warehouse=warehouse,
        sales_rep=actor,
        invoice_number=number.formatted,
        due_date=due,
        status=_S.DRAFT,
        subtotal=totals.subtotal,
        discount_amount=totals.discount,
        tax_amount=totals.tax,
        total=totals.total,
        replaces=replaces,
        notes=(notes or "").strip() or None,
        extra_data=extra_data or {},
    )
    InvoiceItem.objects.bulk_create(
        [
            InvoiceItem(
                invoice=invoice,
                product_id=item.product_id,
                description=item.description or None,
                quantity=item.quantity,
                unit_price=item.unit_price,
            )
            for item in items
        ]
    )
    record(
        action=AuditAction.INVOICE_CREATED,
        organization=organization,
        actor=actor,
        resource=invoice,
        new_values=_snapshot(invoice),
        approval_id=discount_approval_id if totals.discount else None,
        metadata={
            "client_id": str(client.pk),
            "warehouse_id": str(warehouse.pk),
            "lines": len(items),
        },
        context=audit_context,
    )
    logger.info(
        "invoice.created",
        extra={
            "organization_id": str(organization.pk),
            "invoice_id": str(invoice.pk),
            "lines": len(items),
        },
    )
    return invoice


# --------------------------------------------------------------------------- #
# Confirm
# --------------------------------------------------------------------------- #


@transaction.atomic
def confirm_invoice(
    organization: Organization,
    invoice_id: Any,
    *,
    actor: Any,
    upfront_payment: Decimal | int | str = 0,
    credit_approval_id: Any = None,
    audit_context: AuditContext | None = None,
) -> Invoice:
    """
    Confirm a sale atomically: lock the invoice, then the client, validate
    credit, deduct stock through the ledger, freeze totals, refresh the client
    balance, audit. Any failure, including INSUFFICIENT_STOCK, rolls back
    everything (PRD §22, §42).

    Lock order is invoice -> client -> inventory everywhere. Confirming an
    invoice that is already confirmed raises InvalidInvoiceTransitionError, so
    a double submit can never deduct stock twice.

    `upfront_payment` is the amount the caller records against this invoice in
    the same transaction; it reduces the credit exposure checked here.
    """
    ports = get_invoice_ports()
    ports.authorize(actor, organization, PERM_INVOICE_CONFIRM)

    invoice = _lock_invoice(organization, invoice_id)
    _require_status(invoice, CONFIRMABLE_STATUSES, "confirmed")

    stock_lines = _stock_lines(invoice)
    if not stock_lines:
        msg = "An invoice without lines cannot be confirmed."
        raise InvoiceValidationError(msg, detail={"invoice_id": str(invoice.pk)})

    total = invoice.subtotal - invoice.discount_amount + invoice.tax_amount
    client = _lock_client(invoice)
    credit_override_used = _enforce_credit_limit(
        organization,
        actor,
        invoice,
        client,
        total,
        upfront_payment,
        credit_approval_id,
    )

    before = _snapshot(invoice)
    ports.deduct_stock(invoice, stock_lines)

    invoice.status = _S.CONFIRMED
    invoice.confirmed_by = actor
    invoice.issued_at = timezone.now()
    # Frozen from here on.
    invoice.total = total
    invoice.save(
        update_fields=["status", "confirmed_by", "issued_at", "total", "updated_at"]
    )

    _refresh_client_balance(client)
    record(
        action=AuditAction.INVOICE_CONFIRMED,
        organization=organization,
        actor=actor,
        resource=invoice,
        previous_values=before,
        new_values=_snapshot(invoice),
        approval_id=credit_approval_id if credit_override_used else None,
        metadata={
            "lines": len(stock_lines),
            "credit_override": credit_override_used,
        },
        context=audit_context,
    )
    transaction.on_commit(partial(ports.publish, EVENT_INVOICE_CONFIRMED, invoice))
    logger.info(
        "invoice.confirmed",
        extra={"organization_id": str(organization.pk), "invoice_id": str(invoice.pk)},
    )
    return invoice


# --------------------------------------------------------------------------- #
# Void and correct
# --------------------------------------------------------------------------- #


@transaction.atomic
def void_invoice(
    organization: Organization,
    invoice_id: Any,
    *,
    actor: Any,
    reason: str,
    approval_id: Any = None,
    audit_context: AuditContext | None = None,
) -> Invoice:
    """
    Void an invoice. The record stays forever (PRD §44).

    A Draft or Pending invoice never moved stock, so it is simply cancelled.
    A Confirmed or Overdue one returns its stock through reversing ledger
    entries. An invoice with payments is refused: reverse those first.

    The approval port decides whether this void needs an approval and, if so,
    verifies and consumes `approval_id` (PRD §30). Lock order is
    invoice -> client -> inventory.
    """
    ports = get_invoice_ports()
    ports.authorize(actor, organization, PERM_INVOICE_VOID)

    reason = (reason or "").strip()
    if len(reason) < MIN_VOID_REASON_LENGTH:
        msg = f"A reason of at least {MIN_VOID_REASON_LENGTH} characters is required."
        raise InvoiceValidationError(msg, detail={"reason": reason})

    invoice = _lock_invoice(organization, invoice_id)
    if invoice.status in PAID_STATUSES:
        raise InvoiceHasPaymentsError(
            detail={"invoice_id": str(invoice.pk), "status": str(invoice.status)}
        )
    _require_status(invoice, VOIDABLE_STATUSES, "voided")

    stock_was_deducted = invoice.status in VOIDABLE_CONFIRMED_STATUSES
    client = _lock_client(invoice) if stock_was_deducted else None
    if stock_was_deducted and ports.has_payments(invoice):
        raise InvoiceHasPaymentsError(detail={"invoice_id": str(invoice.pk)})

    ports.require_approval(
        actor=actor,
        organization=organization,
        action=APPROVAL_INVOICE_VOID,
        resource=invoice,
        approval_id=approval_id,
    )

    before = _snapshot(invoice)
    if stock_was_deducted:
        ports.restore_stock(invoice, _stock_lines(invoice))

    invoice.status = _S.CANCELLED
    invoice.voided_by = actor
    invoice.voided_at = timezone.now()
    invoice.void_reason = reason
    invoice.save(
        update_fields=[
            "status",
            "voided_by",
            "voided_at",
            "void_reason",
            "updated_at",
        ]
    )

    if client is not None:
        _refresh_client_balance(client)
    record(
        action=AuditAction.INVOICE_VOIDED,
        organization=organization,
        actor=actor,
        resource=invoice,
        previous_values=before,
        new_values=_snapshot(invoice),
        reason=reason,
        approval_id=approval_id,
        metadata={"stock_restored": stock_was_deducted},
        context=audit_context,
    )
    transaction.on_commit(partial(ports.publish, EVENT_INVOICE_VOIDED, invoice))
    logger.info(
        "invoice.voided",
        extra={"organization_id": str(organization.pk), "invoice_id": str(invoice.pk)},
    )
    return invoice


@transaction.atomic
def void_and_correct(
    organization: Organization,
    invoice_id: Any,
    *,
    actor: Any,
    reason: str,
    lines: Sequence[InvoiceLineInput],
    due_date: date | None = None,
    discount_amount: Decimal | int | str | None = None,
    discount_approval_id: Any = None,
    notes: str | None = None,
    approval_id: Any = None,
    audit_context: AuditContext | None = None,
) -> Invoice:
    """
    The PRD's correction flow: Original Invoice > Void > Corrected Invoice.
    Both steps share one transaction, so there is never a voided original
    without its replacement. The corrected invoice is a Draft linked back
    through `replaces`; confirm it like any other.

    The actor needs both the void and the create permission, and the client
    must still be active. `approval_id` covers the void;
    `discount_approval_id` covers any discount on the corrected invoice.
    """
    original = void_invoice(
        organization,
        invoice_id,
        actor=actor,
        reason=reason,
        approval_id=approval_id,
        audit_context=audit_context,
    )
    return create_invoice(
        organization,
        actor=actor,
        client_id=original.client_id,
        warehouse_id=original.warehouse_id,
        lines=lines,
        due_date=due_date,
        discount_amount=(
            original.discount_amount if discount_amount is None else discount_amount
        ),
        discount_approval_id=discount_approval_id,
        notes=(original.notes or "") if notes is None else notes,
        extra_data=original.extra_data,
        replaces=original,
        audit_context=audit_context,
    )


# --------------------------------------------------------------------------- #
# Batch job
# --------------------------------------------------------------------------- #


def mark_overdue_invoices(
    *,
    today: date | None = None,
    batch_size: int = OVERDUE_BATCH_SIZE,
) -> int:
    """
    Move Confirmed invoices past their due date to Overdue. Run on a schedule.

    Cross-tenant by design (a system job). Works in short bounded transactions
    so it never holds long locks or loads the table into memory. Partially paid
    invoices keep their status; `InvoiceQuerySet.overdue()` still finds them by
    due date. The update re-checks the status, so an invoice voided between the
    read and the write is left alone.
    """
    today = today or timezone.localdate()
    updated = 0
    while True:
        ids = list(
            Invoice.objects.not_voided()
            .filter(status=_S.CONFIRMED, due_date__lt=today)
            .order_by()
            .values_list("pk", flat=True)[:batch_size]
        )
        if not ids:
            break
        with transaction.atomic():
            updated += Invoice.objects.filter(pk__in=ids, status=_S.CONFIRMED).update(
                status=_S.OVERDUE,
                updated_at=timezone.now(),
            )
    logger.info(
        "invoice.overdue_sweep",
        extra={"updated": updated, "as_of": today.isoformat()},
    )
    return updated


# --------------------------------------------------------------------------- #
# Reads
# --------------------------------------------------------------------------- #


def get_invoice(organization: Organization, invoice_id: Any, *, actor: Any) -> Invoice:
    """One invoice with parties, items and products loaded in a fixed number of queries."""
    get_invoice_ports().authorize(actor, organization, PERM_INVOICE_VIEW)
    invoice = _get_or_none(
        Invoice.objects.for_organization(organization).for_detail(),
        pk=invoice_id,
    )
    if invoice is None:
        raise InvoiceNotFoundError(detail={"invoice_id": str(invoice_id)})
    return invoice


def list_invoices(
    organization: Organization,
    *,
    actor: Any,
    filters: InvoiceFilters | None = None,
    cursor: tuple[datetime, Any] | None = None,
    limit: int = 50,
) -> list[Invoice]:
    """
    One keyset page, newest first. Pass the (created_at, pk) of the last row as
    `cursor` to get the next page; cost does not grow with depth.
    """
    get_invoice_ports().authorize(actor, organization, PERM_INVOICE_VIEW)
    qs = _filtered_invoices(organization, filters or InvoiceFilters())
    return list(qs.for_list().page_after(cursor, limit))


def get_invoice_summary(
    organization: Organization,
    *,
    actor: Any,
    filters: InvoiceFilters | None = None,
) -> dict[str, Any]:
    """
    The Summary block of the invoice list page (PRD §38): count and money totals
    for the filtered invoices, excluding voided ones so cancelled paper never
    inflates sales, plus how many voided invoices matched. Two queries.
    """
    get_invoice_ports().authorize(actor, organization, PERM_INVOICE_VIEW)
    matching = _filtered_invoices(organization, filters or InvoiceFilters())
    summary = matching.not_voided().totals()
    summary["voided_count"] = matching.voided().count()
    return summary


def available_actions(invoice: Invoice, permissions: Collection[str]) -> list[str]:
    """
    Actions the holder of `permissions` may attempt on this invoice, so the
    frontend never re-implements status rules (PRD §39). Advisory only: every
    action is re-validated when it runs (PRD §29).
    """
    if invoice.is_voided:
        return []
    actions: list[str] = []
    if invoice.status in CONFIRMABLE_STATUSES and PERM_INVOICE_CONFIRM in permissions:
        actions.append(ACTION_CONFIRM)
    if invoice.status in VOIDABLE_STATUSES and PERM_INVOICE_VOID in permissions:
        actions.append(ACTION_VOID)
        if PERM_INVOICE_CREATE in permissions:
            actions.append(ACTION_VOID_AND_CORRECT)
    return actions


def _filtered_invoices(organization: Organization, f: InvoiceFilters):
    """The tenant's invoices narrowed by `f`. A malformed id matches nothing."""
    qs = Invoice.objects.for_organization(organization)
    try:
        if f.client_id is not None:
            qs = qs.filter(client_id=f.client_id)
        if f.sales_rep_id is not None:
            qs = qs.filter(sales_rep_id=f.sales_rep_id)
        if f.warehouse_id is not None:
            qs = qs.filter(warehouse_id=f.warehouse_id)
    except _BAD_ID_ERRORS:
        return qs.none()
    if f.statuses:
        qs = qs.with_status(*f.statuses)
    if f.search:
        qs = qs.search(f.search)
    qs = qs.issued_between(f.issued_from, f.issued_to).due_between(f.due_from, f.due_to)
    if f.overdue_only:
        qs = qs.overdue(timezone.localdate())
    return qs


def get_invoice_audit_trail(
    organization: Organization,
    invoice_id: Any,
    *,
    actor: Any,
    cursor: tuple[datetime, Any] | None = None,
    limit: int = 50,
) -> list[AuditLog]:
    """
    The Audit tab of the invoice detail page (PRD §31, §38): every recorded
    action on one invoice, newest first, keyset paginated. Served by the
    (resource_type, resource_id) index. Requires audit access, which is separate
    from invoice access because audit data is sensitive.
    """
    get_invoice_ports().authorize(actor, organization, PERM_AUDIT_VIEW)
    invoice = _get_or_none(
        Invoice.objects.for_organization(organization).only("pk"),
        pk=invoice_id,
    )
    if invoice is None:
        raise InvoiceNotFoundError(detail={"invoice_id": str(invoice_id)})

    qs = AuditLog.objects.filter(
        organization=organization,
        resource_type=Invoice._meta.label_lower,
        resource_id=str(invoice.pk),
    ).order_by("-created_at", "-pk")
    if cursor is not None:
        created_at, pk = cursor
        qs = qs.filter(Q(created_at__lt=created_at) | Q(created_at=created_at, pk__lt=pk))
    return list(qs[: max(1, min(limit, MAX_PAGE_SIZE))])
