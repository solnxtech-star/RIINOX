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
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from core.applications.invoice.exceptions import InvalidInvoiceTransitionError
from core.applications.invoice.exceptions import InvoiceHasPaymentsError
from core.applications.invoice.exceptions import InvoiceNotFoundError
from core.applications.invoice.exceptions import InvoiceValidationError
from core.applications.invoice.models import Client
from core.applications.invoice.models import Invoice
from core.applications.invoice.models import InvoiceItem
from core.applications.invoice.services.documents import allocate_document_number
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
MAX_MONEY: Final = Decimal("9999999999.99")  # DecimalField(max_digits=12, decimal_places=2)
MAX_INVOICE_LINES: Final = 500
MAX_LINE_QUANTITY: Final = 1_000_000
MIN_VOID_REASON_LENGTH: Final = 5
DEFAULT_PAYMENT_TERMS_DAYS: Final = 30
OVERDUE_BATCH_SIZE: Final = 1000
DEFAULT_PAGE_SIZE: Final = 50
MAX_PAGE_SIZE: Final = 200

CONFIRMABLE_STATUSES: Final = (_S.DRAFT, _S.PENDING)
# Confirmed invoices that have not been paid against: the only ones a void can reverse.
VOIDABLE_CONFIRMED_STATUSES: Final = (_S.CONFIRMED, _S.OVERDUE)
VOIDABLE_STATUSES: Final = (_S.DRAFT, _S.PENDING, *VOIDABLE_CONFIRMED_STATUSES)
PAID_STATUSES: Final = (_S.PARTIALLY_PAID, _S.PAID)


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
    """Round to two decimal places, raising if the value is not a valid money amount."""
    try:
        return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError) as exc:
        msg = "Invalid money amount."
        raise InvoiceValidationError(msg, detail={"value": str(value)}) from exc


def _model(app_label: str, name: str):
    # Resolved lazily so this module needs no hard import of the products and
    # warehouse apps.
    return apps.get_model(app_label, name)


def _key(value: Any) -> str:
    """Canonical text form of an identifier, so 'ABC...' and 'abc...' UUIDs compare equal."""
    return str(value).strip().lower()


def _clamp_limit(limit: int) -> int:
    return max(1, min(limit, MAX_PAGE_SIZE))


def _snapshot(invoice: Invoice) -> dict[str, Any]:
    """JSON-safe state for audit before/after values."""
    return {
        "invoice_number": invoice.invoice_number,
        "status": str(invoice.status),
        "subtotal": str(invoice.subtotal),
        "discount_amount": str(invoice.discount_amount),
        "tax_amount": str(invoice.tax_amount),
        "total": str(invoice.total),
        "is_voided": invoice.is_voided,
        "replaces_id": str(invoice.replaces_id) if invoice.replaces_id else None,
    }


def _require_active_client(client: Client) -> None:
    if not client.is_active or client.status != CustomerStatusChoices.ACTIVE:
        msg = "This client is not active."
        raise InvoiceValidationError(msg, detail={"client_id": str(client.pk)})


def _load_client(organization: Organization, client_id: Any) -> Client:
    client = Client.objects.for_organization(organization).filter(pk=client_id).first()
    if client is None:
        msg = "Client not found."
        raise InvoiceValidationError(msg, detail={"client_id": str(client_id)})
    _require_active_client(client)
    return client


def _load_warehouse(organization: Organization, warehouse_id: Any):
    warehouse = (
        _model("warehouse", "Warehouse").objects.filter(
            organization=organization, pk=warehouse_id).first()
    )
    if warehouse is None:
        msg = "Warehouse not found."
        raise InvoiceValidationError(msg, detail={"warehouse_id": str(warehouse_id)})
    return warehouse


def _resolve_due_date(due_date: date | None) -> date:
    # Server-local date. Per-organization time zones are not modelled yet.
    today = timezone.localdate()
    if due_date is None:
        return today + timedelta(days=DEFAULT_PAYMENT_TERMS_DAYS)
    if due_date < today:
        msg = "The due date cannot be in the past."
        raise InvoiceValidationError(msg, detail={"due_date": due_date.isoformat()})
    return due_date


def _price_lines(organization: Organization, lines: Sequence[InvoiceLineInput]) -> list[_PricedItem]:
    """
    Validate lines and price them from the organization's own products in one query.
    Raise InvoiceValidationError for any problem, with a `detail` dict that
    can be used to highlight the offending line(s) in the UI.
    """
    if not lines:
        msg = "An invoice needs at least one line."
        raise InvoiceValidationError(msg, detail={"lines": 0})
    if len(lines) > MAX_INVOICE_LINES:
        msg = f"An invoice can have at most {MAX_INVOICE_LINES} lines."
        raise InvoiceValidationError(msg, detail={"lines": len(lines)})

    seen: set[str] = set()
    for line in lines:
        key = _key(line.product_id)
        if key in seen:
            msg = "A product can appear only once per invoice."
            raise InvoiceValidationError(msg, detail={"product_id": key})
        seen.add(key)
        quantity = line.quantity
        if isinstance(quantity, bool) or not isinstance(quantity, int) or not 1 <= quantity <= MAX_LINE_QUANTITY:
            msg = f"Quantity must be a whole number between 1 and {MAX_LINE_QUANTITY}."
            raise InvoiceValidationError(msg, detail={"product_id": key, "quantity": str(quantity)})

    products = {
        _key(p.pk): p
        for p in _model("products", "Product")
        .objects.filter(organization=organization, pk__in=[line.product_id for line in lines])
        .only("pk", "customer_sale_price")
    }
    missing = sorted(seen - products.keys())
    if missing:
        msg = "Some products were not found."
        raise InvoiceValidationError(msg, detail={"product_ids": missing})

    priced: list[_PricedItem] = []
    for line in lines:
        product = products[_key(line.product_id)]
        price = product.customer_sale_price
        if price is None or price < 0:
            msg = "Product has no valid sale price."
            raise InvoiceValidationError(msg, detail={"product_id": str(product.pk)})
        priced.append(
            _PricedItem(
                product_id=product.pk,
                quantity=line.quantity,
                unit_price=_money(price),
                description=line.description.strip()[:255],
            )
        )
    return priced


def _calculate_totals(
    organization: Organization,
    actor: Any,
    items: Sequence[_PricedItem],
    discount_amount: Decimal | int | str,
) -> _Totals:
    ports = get_invoice_ports()
    subtotal = _money(sum((i.unit_price * i.quantity for i in items), Decimal(0)))
    discount = _money(discount_amount)
    if discount < 0:
        msg = "The discount cannot be negative."
        raise InvoiceValidationError(msg, detail={"discount": str(discount)})
    if discount > subtotal:
        msg = "The discount cannot exceed the subtotal."
        raise InvoiceValidationError(
            msg, detail={"subtotal": str(subtotal), "discount": str(discount)}
        )
    if discount:
        ports.validate_discount(
            actor=actor, organization=organization, subtotal=subtotal, discount=discount
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
    except Invoice.DoesNotExist as exc:  # pylint: disable=no-member
        raise InvoiceNotFoundError(detail={"invoice_id": str(invoice_id)}) from exc


def _require_status(invoice: Invoice, allowed: Sequence[str], action: str) -> None:
    if invoice.is_voided or invoice.status not in allowed:
        msg = f"An invoice that is {invoice.status} cannot be {action}."
        raise InvalidInvoiceTransitionError(
            msg,
            detail={"status": str(invoice.status), "action": action, "invoice_id": str(invoice.pk)},
        )


def _stock_lines(invoice: Invoice) -> list[StockLine]:
    """Quantities per product, sorted by product id so inventory locks are taken in a stable order."""
    rows = InvoiceItem.objects.for_invoice(invoice).order_by("product_id").values_list("product_id", "quantity")
    return [StockLine(product_id=product_id, quantity=quantity) for product_id, quantity in rows]


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
    notes: str = "",
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

    client = _load_client(organization, client_id)
    warehouse = _load_warehouse(organization, warehouse_id)
    due = _resolve_due_date(due_date)
    items = _price_lines(organization, lines)
    totals = _calculate_totals(organization, actor, items, discount_amount)
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
        notes=notes.strip() or None,
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
        metadata={
            "client_id": str(client.pk),
            "warehouse_id": str(warehouse.pk),
            "lines": len(items)
        },
        context=audit_context,
    )
    logger.info(
        "invoice.created",
        extra={
            "organization_id": str(organization.pk),
            "invoice_id": str(invoice.pk), "lines": len(items)
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
    audit_context: AuditContext | None = None,
) -> Invoice:
    """
    Confirm a sale atomically: lock, validate, deduct stock through the ledger,
    freeze totals, refresh the client balance, audit. Any failure, including
    INSUFFICIENT_STOCK, rolls back everything (PRD §22, §42).
    """
    ports = get_invoice_ports()
    ports.authorize(actor, organization, PERM_INVOICE_CONFIRM)

    invoice = _lock_invoice(organization, invoice_id)
    _require_status(invoice, CONFIRMABLE_STATUSES, "confirmed")
    _require_active_client(invoice.client)

    stock_lines = _stock_lines(invoice)
    if not stock_lines:
        msg = "An invoice without lines cannot be confirmed."
        raise InvoiceValidationError(msg, detail={"invoice_id": str(invoice.pk)})

    before = _snapshot(invoice)
    ports.deduct_stock(invoice, stock_lines)

    invoice.status = _S.CONFIRMED
    invoice.confirmed_by = actor
    invoice.total = invoice.subtotal - invoice.discount_amount + invoice.tax_amount  # frozen from here on
    invoice.save(update_fields=["status", "confirmed_by", "total", "updated_at"])

    ports.refresh_client_balance(invoice.client)
    record(
        action=AuditAction.INVOICE_CONFIRMED,
        organization=organization,
        actor=actor,
        resource=invoice,
        previous_values=before,
        new_values=_snapshot(invoice),
        metadata={"lines": len(stock_lines)},
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
    """
    ports = get_invoice_ports()
    ports.authorize(actor, organization, PERM_INVOICE_VOID)

    reason = (reason or "").strip()
    if len(reason) < MIN_VOID_REASON_LENGTH:
        msg = f"A reason of at least {MIN_VOID_REASON_LENGTH} characters is required."
        raise InvoiceValidationError(msg, detail={"min_length": MIN_VOID_REASON_LENGTH})

    invoice = _lock_invoice(organization, invoice_id)
    if invoice.status in PAID_STATUSES:
        raise InvoiceHasPaymentsError(detail={"invoice_id": str(invoice.pk), "status": str(invoice.status)})
    _require_status(invoice, VOIDABLE_STATUSES, "voided")

    stock_was_deducted = invoice.status in VOIDABLE_CONFIRMED_STATUSES
    if stock_was_deducted and ports.has_payments(invoice):
        raise InvoiceHasPaymentsError(detail={"invoice_id": str(invoice.pk)})

    before = _snapshot(invoice)
    if stock_was_deducted:
        ports.restore_stock(invoice, _stock_lines(invoice))

    invoice.status = _S.CANCELLED
    invoice.is_voided = True
    invoice.voided_by = actor
    invoice.voided_at = timezone.now()
    invoice.void_reason = reason
    invoice.save(update_fields=["status", "is_voided", "voided_by", "voided_at", "void_reason", "updated_at"])

    if stock_was_deducted:
        ports.refresh_client_balance(invoice.client)
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
    notes: str | None = None,
    approval_id: Any = None,
    audit_context: AuditContext | None = None,
) -> Invoice:
    """
    The PRD's correction flow: Original Invoice > Void > Corrected Invoice.
    Both steps share one transaction, so there is never a voided original
    without its replacement. The corrected invoice is a Draft linked back
    through `replaces`; confirm it like any other. Anything not supplied is
    carried over from the original: discount, notes, extra data and, if it is
    still in the future, the due date.
    """
    original = void_invoice(
        organization,
        invoice_id,
        actor=actor,
        reason=reason,
        approval_id=approval_id,
        audit_context=audit_context,
    )
    if due_date is None and original.due_date >= timezone.localdate():
        due_date = original.due_date
    return create_invoice(
        organization,
        actor=actor,
        client_id=original.client_id,
        warehouse_id=original.warehouse_id,
        lines=lines,
        due_date=due_date,
        discount_amount=original.discount_amount if discount_amount is None else discount_amount,
        notes=(original.notes or "") if notes is None else notes,
        extra_data=original.extra_data,
        replaces=original,
        audit_context=audit_context,
    )


# --------------------------------------------------------------------------- #
# Batch job
# --------------------------------------------------------------------------- #


def mark_overdue_invoices(*, today: date | None = None, batch_size: int = OVERDUE_BATCH_SIZE) -> int:
    """
    Move Confirmed invoices past their due date to Overdue. Run on a schedule.

    Cross-tenant by design (a system job). Works in short bounded transactions
    so it never holds long locks or loads the table into memory. Partially paid
    invoices keep their status; `InvoiceQuerySet.overdue()` still finds them by
    due date. A bulk status sweep writes no per-invoice audit rows: it is a
    deterministic function of the due date, not a user action.
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
                status=_S.OVERDUE, updated_at=timezone.now()
            )
    logger.info("invoice.overdue_sweep", extra={"updated": updated, "as_of": today.isoformat()})
    return updated


# --------------------------------------------------------------------------- #
# Reads
# --------------------------------------------------------------------------- #


def get_invoice(organization: Organization, invoice_id: Any, *, actor: Any) -> Invoice:
    """One invoice with parties, items and products loaded in a fixed number of queries."""
    get_invoice_ports().authorize(actor, organization, PERM_INVOICE_VIEW)
    try:
        return Invoice.objects.for_organization(organization).for_detail().get(pk=invoice_id)
    except Invoice.DoesNotExist as exc:
        raise InvoiceNotFoundError(detail={"invoice_id": str(invoice_id)}) from exc


def list_invoices(
    organization: Organization,
    *,
    actor: Any,
    filters: InvoiceFilters | None = None,
    cursor: tuple[datetime, Any] | None = None,
    limit: int = DEFAULT_PAGE_SIZE,
) -> list[Invoice]:
    """
    One keyset page, newest first. Pass the (created_at, pk) of the last row as
    `cursor` to get the next page; cost does not grow with depth.
    """
    get_invoice_ports().authorize(actor, organization, PERM_INVOICE_VIEW)
    f = filters or InvoiceFilters()

    qs = Invoice.objects.for_organization(organization)
    if f.statuses:
        qs = qs.with_status(*f.statuses)
    if f.client_id is not None:
        qs = qs.filter(client_id=f.client_id)
    if f.sales_rep_id is not None:
        qs = qs.filter(sales_rep_id=f.sales_rep_id)
    if f.search:
        qs = qs.search(f.search)
    qs = qs.issued_between(f.issued_from, f.issued_to).due_between(f.due_from, f.due_to)
    if f.overdue_only:
        qs = qs.overdue(timezone.localdate())
    return list(qs.for_list().page_after(cursor, limit))


def get_invoice_audit_trail(
    organization: Organization,
    invoice_id: Any,
    *,
    actor: Any,
    cursor: tuple[datetime, Any] | None = None,
    limit: int = DEFAULT_PAGE_SIZE,
) -> list[AuditLog]:
    """
    The Audit tab of the invoice detail page (PRD §14, §31): every recorded
    action on one invoice, newest first, keyset paginated. Served by the
    existing (resource_type, resource_id) index. Requires audit access, which
    is separate from invoice access because audit data is sensitive.
    """
    get_invoice_ports().authorize(actor, organization, PERM_AUDIT_VIEW)
    if not Invoice.objects.for_organization(organization).filter(pk=invoice_id).exists():
        raise InvoiceNotFoundError(detail={"invoice_id": str(invoice_id)})

    qs = AuditLog.objects.filter(
        organization=organization,
        resource_type=Invoice._meta.label_lower,  # noqa: SLF001
        resource_id=str(invoice_id),
    ).order_by("-created_at", "-pk")
    if cursor is not None:
        created_at, pk = cursor
        qs = qs.filter(Q(created_at__lt=created_at) | Q(created_at=created_at, pk__lt=pk))
    return list(qs[: _clamp_limit(limit)])
