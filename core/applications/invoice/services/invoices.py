from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from datetime import tzinfo
from decimal import ROUND_FLOOR
from decimal import ROUND_HALF_UP
from decimal import Decimal
from decimal import InvalidOperation
from functools import partial
from typing import TYPE_CHECKING
from typing import Any
from typing import Final
from typing import NamedTuple
from zoneinfo import ZoneInfo
from zoneinfo import ZoneInfoNotFoundError

from django.apps import apps
from django.core.exceptions import ObjectDoesNotExist
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from core.applications.customers.models import Customer
from core.applications.customers.models import CustomerAddress
from core.applications.invoice.exceptions import ApprovalRequiredError
from core.applications.invoice.exceptions import CreditLimitExceededError
from core.applications.invoice.exceptions import InvalidInvoiceTransitionError
from core.applications.invoice.exceptions import InvoiceAlreadyIssuedForSaleError
from core.applications.invoice.exceptions import InvoiceHasPaymentsError
from core.applications.invoice.exceptions import InvoiceNotFoundError
from core.applications.invoice.exceptions import InvoiceValidationError
from core.applications.invoice.models import Invoice
from core.applications.invoice.models import InvoiceItem
from core.applications.invoice.queryset import MAX_PAGE_SIZE
from core.applications.invoice.services.documents import allocate_document_number
from core.applications.invoice.services.ports import APPROVAL_CREDIT_OVERRIDE
from core.applications.invoice.services.ports import APPROVAL_INVOICE_VOID
from core.applications.invoice.services.ports import EVENT_INVOICE_CONFIRMED
from core.applications.invoice.services.ports import EVENT_INVOICE_OVERDUE
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
from core.helper.enums import CustomerStatus
from core.helper.enums import InvoiceStatusChoices
from core.helper.enums import UnitOfMeasureChoices

if TYPE_CHECKING:
    from collections.abc import Collection
    from collections.abc import Sequence
    from datetime import date
    from datetime import datetime

    from core.applications.invoice.services.ports import SaleSnapshot
    from core.applications.notification.audit.context import AuditContext
    from core.applications.users.models import Organization

logger = logging.getLogger(__name__)

_S = InvoiceStatusChoices

# --------------------------------------------------------------------------- #
# Limits and status rules
# --------------------------------------------------------------------------- #

CENT: Final = Decimal("0.01")
RATE_STEP: Final = Decimal("0.01")
MULTIPLIER_STEP: Final = Decimal("0.001")
# DecimalField(max_digits=12, decimal_places=2) holds at most 10 integer digits.
MAX_MONEY: Final = Decimal("9999999999.99")
# DecimalField(max_digits=10, decimal_places=3) holds at most 7 integer digits.
MAX_MULTIPLIER: Final = Decimal("9999999.999")
MAX_INVOICE_LINES: Final = 500
MAX_LINE_QUANTITY: Final = 1_000_000
MIN_VOID_REASON_LENGTH: Final = 5
MAX_IDEMPOTENCY_KEY_LENGTH: Final = 64
OVERDUE_BATCH_SIZE: Final = 1000

# PENDING is reserved for invoices held for approval before confirmation. No
# service creates it yet, but it is confirmable and voidable so the approval
# flow (PRD §30) can adopt it without touching the lifecycle rules.
CONFIRMABLE_STATUSES: Final = (_S.DRAFT, _S.PENDING)
# Confirmed invoices with no payments against them: the only ones a void can
# reverse stock for.
VOIDABLE_CONFIRMED_STATUSES: Final = (_S.CONFIRMED, _S.OVERDUE)
VOIDABLE_STATUSES: Final = (_S.DRAFT, _S.PENDING, *VOIDABLE_CONFIRMED_STATUSES)
PAID_STATUSES: Final = (_S.PARTIALLY_PAID, _S.PAID)

# Actions an invoice can offer, as shown on the detail page (PRD §38).
ACTION_EDIT: Final = "edit"
ACTION_CONFIRM: Final = "confirm"
ACTION_VOID: Final = "void"
ACTION_VOID_AND_CORRECT: Final = "void_and_correct"

# Names of the database constraints the services translate into domain errors.
_LIVE_INVOICE_CONSTRAINT: Final = "unique_live_invoice_per_sale"
_IDEMPOTENCY_CONSTRAINT: Final = "unique_invoice_idempotency_key_per_org"

# Attribute on Organization holding its IANA timezone name (PRD §7), and the
# attribute on ProductVariant that may override the product's sale price.
# Adjust these two names to match your models.
ORGANIZATION_TIMEZONE_ATTR: Final = "timezone"
VARIANT_PRICE_ATTR: Final = "customer_sale_price"

# Frozen customer details copied onto the invoice (billing/shipping included).
CUSTOMER_SNAPSHOT_FIELDS: Final = (
    "customer_name",
    "customer_company_name",
    "customer_tax_id",
    "customer_email",
    "customer_phone",
    "billing_address",
    "shipping_address",
)

# Raised by Django while converting a malformed id (e.g. "abc" for a UUID or
# integer key) before any SQL runs. Treated as "not found".
_BAD_ID_ERRORS: Final = (ValidationError, ValueError, TypeError)


# --------------------------------------------------------------------------- #
# Inputs and internal value objects
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class InvoiceLineInput:
    """
    What a caller sends per line. Prices are never accepted from the caller.
    `description` is optional custom line text; the product name is used when
    it is empty. `variant_id` selects a product variant. `unit_name` sells the
    line in a unit other than the base one; the multiplier and price for that
    unit come from the server (the `resolve_unit` port), never from the caller.
    """

    product_id: Any
    quantity: int
    description: str = ""
    variant_id: Any = None
    unit_name: str | None = None


@dataclass(frozen=True, slots=True)
class InvoiceFilters:
    statuses: tuple[str, ...] = ()
    customer_id: Any = None
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
    variant_id: Any
    quantity: int
    unit_name: str
    unit_multiplier: Decimal
    unit_price: Decimal
    description: str


@dataclass(frozen=True, slots=True)
class _Totals:
    subtotal: Decimal
    discount: Decimal
    tax: Decimal
    total: Decimal


@dataclass(frozen=True, slots=True)
class _LineAmounts:
    """A line's share of the invoice discount and tax."""

    discount: Decimal
    tax: Decimal
    tax_rate: Decimal


class _Row(NamedTuple):
    """The columns of an invoice line the lifecycle services need."""

    product_id: Any
    quantity: int
    unit_multiplier: Decimal
    unit_price: Decimal
    discount: Decimal
    tax: Decimal


# --------------------------------------------------------------------------- #
# Generic helpers
# --------------------------------------------------------------------------- #


def _decimal(value: Any, step: Decimal, *, field: str) -> Decimal:
    try:
        return Decimal(str(value)).quantize(step, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError) as exc:
        msg = f"Invalid {field}."
        raise InvoiceValidationError(msg, detail={field: str(value)}) from exc


def _money(value: Decimal | int | str) -> Decimal:
    return _decimal(value, CENT, field="amount")


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


def _validate_quantity(quantity: Any, product_key: str) -> None:
    is_whole_number = isinstance(quantity, int) and not isinstance(quantity, bool)
    if not is_whole_number or not 1 <= quantity <= MAX_LINE_QUANTITY:
        msg = f"Quantity must be a whole number between 1 and {MAX_LINE_QUANTITY}."
        raise InvoiceValidationError(
            msg,
            detail={"product_id": product_key, "quantity": str(quantity)},
        )


def _organization_tz(organization: Organization) -> tzinfo:
    """
    The organization's own timezone (PRD §7), so "today" and day boundaries are
    the tenant's, not the server's. Falls back to the project timezone when the
    organization has none or the name is not a valid IANA zone.
    """
    raw = getattr(organization, ORGANIZATION_TIMEZONE_ATTR, None)
    if isinstance(raw, tzinfo):
        return raw
    if raw:
        try:
            return ZoneInfo(str(raw))
        except (ZoneInfoNotFoundError, ValueError, OSError):
            logger.warning(
                "invoice.invalid_organization_timezone",
                extra={"organization_id": str(organization.pk), "timezone": str(raw)},
            )
    return timezone.get_current_timezone()


def _today(organization: Organization) -> date:
    return timezone.now().astimezone(_organization_tz(organization)).date()


def _clean_idempotency_key(key: str | None) -> str | None:
    cleaned = (key or "").strip()
    if not cleaned:
        return None
    if len(cleaned) > MAX_IDEMPOTENCY_KEY_LENGTH:
        msg = f"The idempotency key can have at most {MAX_IDEMPOTENCY_KEY_LENGTH} characters."
        raise InvoiceValidationError(msg, detail={"idempotency_key": cleaned[:MAX_IDEMPOTENCY_KEY_LENGTH]})
    return cleaned


def allocate_amount(amount: Decimal, weights: Sequence[Decimal]) -> list[Decimal]:
    """
    Split `amount` across `weights` in whole cents using the largest-remainder
    method, so the parts always add up to exactly `amount`. Zero weights get
    nothing unless every weight is zero, in which case the split is even.
    """
    count = len(weights)
    if not count:
        return []
    zero = Decimal("0.00")
    if amount == 0:
        return [zero] * count

    total_weight = sum(weights, Decimal(0))
    if total_weight <= 0:
        weights = [Decimal(1)] * count
        total_weight = Decimal(count)

    cents = int((amount / CENT).to_integral_value())
    shares = [Decimal(cents) * w / total_weight for w in weights]
    floors = [int(s.to_integral_value(rounding=ROUND_FLOOR)) for s in shares]
    leftover = cents - sum(floors)
    # Largest fractional part first; the earlier line wins a tie.
    order = sorted(range(count), key=lambda i: (shares[i] - floors[i], -i), reverse=True)
    for i in order[:leftover]:
        floors[i] += 1
    return [Decimal(f) * CENT for f in floors]


def _snapshot(invoice: Invoice) -> dict[str, Any]:
    """JSON-safe state for audit before/after values."""
    return {
        "invoice_number": invoice.invoice_number,
        "status": str(invoice.status),
        "customer_id": str(invoice.customer_id) if invoice.customer_id else None,
        "customer_name": invoice.customer_name,
        "subtotal": str(invoice.subtotal),
        "discount_amount": str(invoice.discount_amount),
        "tax_amount": str(invoice.tax_amount),
        "total": str(invoice.total),
        "amount_paid": str(invoice.amount_paid),
        "due_date": invoice.due_date.isoformat() if invoice.due_date else None,
        "issued_at": invoice.issued_at.isoformat() if invoice.issued_at else None,
        "sale_id": str(invoice.sale_id) if invoice.sale_id else None,
        "replaces_id": str(invoice.replaces_id) if invoice.replaces_id else None,
    }


def _initial_status(total: Decimal) -> str:
    """
    Status of an invoice the moment it is confirmed or issued. Money received
    is applied afterwards by the payments service; only a zero-value invoice
    has nothing left to collect.
    """
    return _S.PAID if total == 0 else _S.CONFIRMED


# --------------------------------------------------------------------------- #
# Customers
# --------------------------------------------------------------------------- #


def _fetch_customer(
    organization: Organization,
    customer_id: Any,
    *,
    lock: bool = False,
) -> Customer:
    """
    The customer scoped to the tenant. A cross-tenant id is indistinguishable
    from a missing one. With `lock=True` the row is locked for update.
    """
    queryset = Customer.objects.filter(organization=organization)
    if lock:
        queryset = queryset.select_for_update()
    try:
        return queryset.get(pk=customer_id)
    except (ObjectDoesNotExist, *_BAD_ID_ERRORS) as exc:
        msg = "Customer not found."
        raise InvoiceValidationError(msg, detail={"customer_id": str(customer_id)}) from exc


def _require_active_customer(customer: Customer) -> None:
    if customer.status != CustomerStatus.ACTIVE:
        msg = "This customer is not active."
        raise InvoiceValidationError(
            msg,
            detail={"customer_id": str(customer.pk), "status": str(customer.status)},
        )


def load_active_customer(
    organization: Organization,
    customer_id: Any,
    *,
    lock: bool = False,
) -> Customer:
    """
    Load a customer and require it to be active. Pass `lock=True` when the
    invoice will change money or stock: the status is then checked on the
    locked row, so a customer blocked a moment ago cannot slip through.
    """
    customer = _fetch_customer(organization, customer_id, lock=lock)
    _require_active_customer(customer)
    return customer


def _sync_balance(customer: Customer) -> None:
    """
    Recompute the customer's cached balance and reload it. Call only with the
    customer row locked: without the lock, two concurrent confirmations each
    compute from data that excludes the other's uncommitted invoice and the
    last writer leaves the balance short. The lock serializes invoices of one
    customer only; other customers and tenants are unaffected.
    """
    get_invoice_ports().refresh_customer_balance(customer)
    customer.refresh_from_db(fields=["balance"])


def _format_address(address: CustomerAddress) -> str:
    parts = (
        address.street,
        address.city,
        address.state,
        address.postal_code,
        address.country,
    )
    return ", ".join(part.strip() for part in parts if part and part.strip())


def _pick_address(addresses: Sequence[CustomerAddress], wanted: str) -> str:
    """
    The customer's default address of the wanted type, else the first one that
    fits. Addresses arrive ordered default-first (CustomerAddress.Meta).
    """
    kinds = (wanted, CustomerAddress.AddressType.BOTH)
    for address in addresses:
        if address.address_type in kinds:
            return _format_address(address)
    return ""


def customer_snapshot(
    customer: Customer,
    *,
    billing_address: str | None = None,
    shipping_address: str | None = None,
) -> dict[str, str]:
    """
    The customer details to freeze on an invoice. Addresses already chosen on
    the sale win; otherwise they come from the customer's saved addresses.
    """
    billing = (billing_address or "").strip()
    shipping = (shipping_address or "").strip()
    if not billing or not shipping:
        addresses = list(customer.addresses.all())
        billing = billing or _pick_address(addresses, CustomerAddress.AddressType.BILLING)
        shipping = shipping or _pick_address(addresses, CustomerAddress.AddressType.SHIPPING)
    return {
        "customer_name": customer.name,
        "customer_company_name": customer.company_name,
        "customer_tax_id": customer.tax_id,
        "customer_email": customer.email,
        "customer_phone": customer.phone,
        "billing_address": billing,
        "shipping_address": shipping,
    }


def _resolve_due_date(customer: Customer, issue_date: date, requested: date | None) -> date:
    """
    Due date from the customer's payment terms (net N days) unless a date is
    requested explicitly. A due date can never precede the issue date.
    `issue_date` is the organization's own today.
    """
    if requested is None:
        return issue_date + timedelta(days=customer.payment_terms_days)
    if requested < issue_date:
        msg = "The due date cannot be in the past."
        raise InvoiceValidationError(msg, detail={"due_date": requested.isoformat()})
    return requested


def _validate_upfront_payment(upfront_payment: Decimal | int | str, total: Decimal) -> Decimal:
    paid_now = _money(upfront_payment)
    if not Decimal(0) <= paid_now <= total:
        msg = "The upfront payment must be between zero and the invoice total."
        raise InvoiceValidationError(
            msg,
            detail={"upfront_payment": str(paid_now), "total": str(total)},
        )
    return paid_now


def _enforce_credit_limit(
    organization: Organization,
    actor: Any,
    invoice: Invoice,
    customer: Customer,
    *,
    invoice_total: Decimal,
    paid_now: Decimal,
    approval_id: Any,
) -> bool:
    """
    Block a sale that takes the customer over their credit limit unless the
    approval port allows it (PRD §26, §30).

    Only the unpaid part of the invoice is credit. A credit limit of 0 means
    the customer cannot buy on credit (as the Customer model documents), so any
    unpaid amount needs an override. `customer` must be locked and its balance
    freshly synced, and must not yet include this invoice.

    Returns True when the sale exceeded the limit and went through on an
    override, so the caller records the approval only when it was relied on.
    """
    unpaid = invoice_total - paid_now
    if unpaid <= 0:
        return False

    exposure = customer.balance + unpaid
    if exposure <= customer.credit_limit:
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
        msg = "This sale would take the customer over their credit limit."
        raise CreditLimitExceededError(
            msg,
            detail={
                "credit_limit": str(customer.credit_limit),
                "current_balance": str(customer.balance),
                "invoice_total": str(invoice_total),
                "upfront_payment": str(paid_now),
                "exposure": str(exposure),
            },
        ) from exc
    return True


# --------------------------------------------------------------------------- #
# Warehouses, invoices and lines
# --------------------------------------------------------------------------- #


def _load_warehouse(organization: Organization, warehouse_id: Any):
    warehouse = _get_or_none(
        _model("warehouse", "Warehouse").objects.filter(organization=organization),
        pk=warehouse_id,
    )
    if warehouse is None:
        msg = "Warehouse not found."
        raise InvoiceValidationError(msg, detail={"warehouse_id": str(warehouse_id)})
    return warehouse


def _lock_invoice(organization: Organization, invoice_id: Any) -> Invoice:
    """
    Fetch the invoice scoped to the tenant with a row lock on the invoice only.
    A cross-tenant id is indistinguishable from a missing one.
    """
    try:
        return (
            Invoice.objects.for_organization(organization)
            .select_related("warehouse")
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


def _is_sale_origin(organization: Organization, invoice_id: Any) -> bool:
    invoice = _get_or_none(
        Invoice.objects.for_organization(organization).only("pk", "sale_id"),
        pk=invoice_id,
    )
    return invoice is not None and invoice.sale_id is not None


def _load_lines(invoice: Invoice) -> list[_Row]:
    """The invoice's lines in a stable product order, in one query."""
    rows = (
        InvoiceItem.objects.for_invoice(invoice)
        .order_by("product_id", "pk")
        .values_list(
            "product_id",
            "quantity",
            "unit_multiplier",
            "unit_price",
            "discount_amount",
            "tax_amount",
        )
    )
    return [_Row(*row) for row in rows]


def _assert_lines_match_header(invoice: Invoice, rows: Sequence[_Row]) -> None:
    """
    The header totals must equal the lines they summarize. A mismatch means the
    draft was changed outside these services; refuse to freeze it.
    """
    subtotal = sum((r.quantity * r.unit_price for r in rows), Decimal(0))
    discount = sum((r.discount for r in rows), Decimal(0))
    tax = sum((r.tax for r in rows), Decimal(0))
    if (subtotal, discount, tax) != (
        invoice.subtotal,
        invoice.discount_amount,
        invoice.tax_amount,
    ):
        msg = "The invoice lines do not match its totals."
        raise InvoiceValidationError(
            msg,
            detail={
                "invoice_id": str(invoice.pk),
                "header": {
                    "subtotal": str(invoice.subtotal),
                    "discount_amount": str(invoice.discount_amount),
                    "tax_amount": str(invoice.tax_amount),
                },
                "lines": {
                    "subtotal": str(subtotal),
                    "discount_amount": str(discount),
                    "tax_amount": str(tax),
                },
            },
        )


def _stock_lines_from(rows: Sequence[_Row]) -> list[StockLine]:
    """
    Base-unit quantities per product (quantity x unit multiplier), in the
    stable product order of `rows` so inventory locks are taken consistently.
    Lines for the same product in different units are merged.
    """
    base_units: dict[Any, int] = {}
    for row in rows:
        units = Decimal(row.quantity) * row.unit_multiplier
        if units != units.to_integral_value():
            msg = "A line converts to a fractional number of base units."
            raise InvoiceValidationError(msg, detail={"product_id": str(row.product_id)})
        base_units[row.product_id] = base_units.get(row.product_id, 0) + int(units)
    return [StockLine(product_id=pid, quantity=qty) for pid, qty in base_units.items()]


def _stock_lines(invoice: Invoice) -> list[StockLine]:
    return _stock_lines_from(_load_lines(invoice))


# --------------------------------------------------------------------------- #
# Pricing standalone invoices
# --------------------------------------------------------------------------- #


def _line_key(line: InvoiceLineInput) -> tuple[str, str, str]:
    return (
        str(line.product_id),
        str(line.variant_id) if line.variant_id else "",
        (line.unit_name or "").strip().lower(),
    )


def _validate_lines(lines: Sequence[InvoiceLineInput]) -> None:
    """Shape checks that need no database."""
    if not lines:
        msg = "An invoice needs at least one line."
        raise InvoiceValidationError(msg, detail={"lines": []})
    if len(lines) > MAX_INVOICE_LINES:
        msg = f"An invoice can have at most {MAX_INVOICE_LINES} lines."
        raise InvoiceValidationError(msg, detail={"lines": len(lines)})

    seen: set[tuple[str, str, str]] = set()
    for line in lines:
        key = _line_key(line)
        if key in seen:
            msg = "A product, variant and unit combination can appear only once per invoice."
            raise InvoiceValidationError(
                msg,
                detail={"product_id": key[0], "variant_id": key[1], "unit_name": key[2]},
            )
        seen.add(key)
        _validate_quantity(line.quantity, key[0])


def _resolve_line_unit(
    organization: Organization,
    product: Any,
    variant: Any,
    line: InvoiceLineInput,
    base_price: Decimal,
) -> tuple[str, Decimal, Decimal]:
    """
    (unit name, multiplier, price per line unit). Without a requested unit the
    line is sold in the base unit at the base price. Otherwise the units port
    decides, so the caller can never choose its own conversion or price.
    """
    if not line.unit_name:
        return UnitOfMeasureChoices.PIECE, Decimal(1), base_price

    resolution = get_invoice_ports().resolve_unit(
        organization=organization,
        product=product,
        variant=variant,
        unit_name=line.unit_name,
        base_price=base_price,
    )
    multiplier = _decimal(resolution.multiplier, MULTIPLIER_STEP, field="unit multiplier")
    if not 0 < multiplier <= MAX_MULTIPLIER:
        msg = "The unit multiplier must be greater than zero."
        raise InvoiceValidationError(msg, detail={"product_id": str(product.pk)})
    return resolution.unit_name, multiplier, resolution.unit_price


def _price_lines(
    organization: Organization,
    lines: Sequence[InvoiceLineInput],
) -> list[_PricedItem]:
    """
    Validate lines and price them from the organization's own products (and
    variants) in at most two queries. The line text is snapshotted now: custom
    text if given, else the product name, so a later rename never rewrites a
    historical invoice.
    """
    _validate_lines(lines)
    product_ids = {str(line.product_id) for line in lines}
    variant_ids = {str(line.variant_id) for line in lines if line.variant_id}

    try:
        products = {
            str(p.pk): p
            for p in _model("products", "Product").objects.filter(
                organization=organization,
                pk__in=product_ids,
            )
        }
        variants = (
            {
                str(v.pk): v
                for v in _model("products", "ProductVariant").objects.filter(
                    product__organization=organization,
                    pk__in=variant_ids,
                )
            }
            if variant_ids
            else {}
        )
    except _BAD_ID_ERRORS as exc:
        msg = "One or more product or variant ids are malformed."
        raise InvoiceValidationError(
            msg,
            detail={"product_ids": sorted(product_ids), "variant_ids": sorted(variant_ids)},
        ) from exc

    missing = sorted(product_ids - products.keys())
    if missing:
        msg = "Some products were not found."
        raise InvoiceValidationError(msg, detail={"product_ids": missing})
    missing_variants = sorted(variant_ids - variants.keys())
    if missing_variants:
        msg = "Some variants were not found."
        raise InvoiceValidationError(msg, detail={"variant_ids": missing_variants})

    priced: list[_PricedItem] = []
    for line in lines:
        product = products[str(line.product_id)]
        variant = variants[str(line.variant_id)] if line.variant_id else None
        if variant is not None and str(variant.product_id) != str(product.pk):
            msg = "The variant does not belong to this product."
            raise InvoiceValidationError(
                msg,
                detail={"product_id": str(product.pk), "variant_id": str(variant.pk)},
            )

        variant_price = getattr(variant, VARIANT_PRICE_ATTR, None) if variant is not None else None
        base_price = variant_price if variant_price is not None else product.customer_sale_price
        if base_price is None or base_price < 0:
            msg = "Product has no valid sale price."
            raise InvoiceValidationError(msg, detail={"product_id": str(product.pk)})

        unit_name, multiplier, unit_price = _resolve_line_unit(
            organization, product, variant, line, _money(base_price)
        )
        unit_price = _money(unit_price)
        if unit_price < 0:
            msg = "Product has no valid sale price."
            raise InvoiceValidationError(msg, detail={"product_id": str(product.pk)})

        text = (line.description or "").strip() or (product.name or "").strip()
        priced.append(
            _PricedItem(
                product_id=product.pk,
                variant_id=variant.pk if variant is not None else None,
                quantity=line.quantity,
                unit_name=unit_name,
                unit_multiplier=multiplier,
                unit_price=unit_price,
                description=text[:255],
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


def _allocate_line_amounts(
    items: Sequence[_PricedItem],
    totals: _Totals,
) -> list[_LineAmounts]:
    """
    Push the invoice discount and tax down to the lines so a standalone invoice
    has the same shape as a sale-origin one: every line carries its own
    discount, tax and rate, and the lines add up exactly to the header.

    The discount is shared in proportion to line value and the tax in
    proportion to each line's taxable value, in whole cents. The rate stored on
    a line is its effective rate (tax / taxable value). Tax on an invoice whose
    taxable value is zero has no line to sit on and is refused.
    """
    gross = [i.unit_price * i.quantity for i in items]
    discounts = allocate_amount(totals.discount, gross)
    taxable = [g - d for g, d in zip(gross, discounts, strict=True)]
    if totals.tax > 0 and not any(base > 0 for base in taxable):
        msg = "Tax cannot be charged on an invoice with no taxable amount."
        raise InvoiceValidationError(msg, detail={"tax": str(totals.tax)})
    taxes = allocate_amount(totals.tax, taxable)

    amounts: list[_LineAmounts] = []
    for base, discount, tax in zip(taxable, discounts, taxes, strict=True):
        rate = (tax / base * 100).quantize(RATE_STEP, rounding=ROUND_HALF_UP) if base > 0 else Decimal("0.00")
        amounts.append(_LineAmounts(discount=discount, tax=tax, tax_rate=min(rate, Decimal(100))))
    return amounts


def _build_items(
    invoice: Invoice,
    items: Sequence[_PricedItem],
    amounts: Sequence[_LineAmounts],
) -> list[InvoiceItem]:
    return [
        InvoiceItem(
            invoice=invoice,
            product_id=item.product_id,
            variant_id=item.variant_id,
            description=item.description or None,
            quantity=item.quantity,
            unit_name=item.unit_name,
            unit_multiplier=item.unit_multiplier,
            unit_price=item.unit_price,
            discount_amount=share.discount,
            tax_amount=share.tax,
            tax_rate=share.tax_rate,
        )
        for item, share in zip(items, amounts, strict=True)
    ]


# --------------------------------------------------------------------------- #
# Sale snapshot validation
# --------------------------------------------------------------------------- #


def _validate_sale_snapshot(
    organization: Organization,
    snapshot: SaleSnapshot,
) -> _Totals:
    """
    Check a sale snapshot is safe to turn into an invoice and return its
    totals recomputed from the lines. The header must equal the lines, so the
    invoice's database constraints can never be the first thing to notice a
    broken sale.
    """
    if str(snapshot.organization_id) != str(organization.pk):
        msg = "The sale belongs to another organization."
        raise InvoiceValidationError(msg, detail={"sale_id": str(snapshot.sale_id)})
    if snapshot.is_cancelled:
        msg = "A cancelled sale cannot be invoiced."
        raise InvoiceValidationError(msg, detail={"sale_id": str(snapshot.sale_id)})

    lines = snapshot.lines
    if not lines:
        msg = "A sale without lines cannot be invoiced."
        raise InvoiceValidationError(msg, detail={"sale_id": str(snapshot.sale_id)})
    if len(lines) > MAX_INVOICE_LINES:
        msg = f"An invoice can have at most {MAX_INVOICE_LINES} lines."
        raise InvoiceValidationError(msg, detail={"lines": len(lines)})

    subtotal = discount = tax = Decimal(0)
    for line in lines:
        key = str(line.product_id)
        _validate_quantity(line.quantity, key)

        multiplier = _decimal(line.unit_multiplier, MULTIPLIER_STEP, field="unit multiplier")
        if not 0 < multiplier <= MAX_MULTIPLIER:
            msg = "The unit multiplier must be greater than zero."
            raise InvoiceValidationError(msg, detail={"product_id": key})

        unit_price = _money(line.unit_price)
        line_discount = _money(line.discount)
        line_tax = _money(line.tax)
        gross = unit_price * line.quantity
        if unit_price < 0 or line_discount < 0 or line_tax < 0 or line_discount > gross:
            msg = "A line has an invalid price, discount or tax."
            raise InvoiceValidationError(
                msg,
                detail={
                    "product_id": key,
                    "unit_price": str(unit_price),
                    "discount": str(line_discount),
                    "tax": str(line_tax),
                },
            )
        if not Decimal(0) <= _decimal(line.tax_rate, RATE_STEP, field="tax rate") <= Decimal(100):
            msg = "A line has an invalid tax rate."
            raise InvoiceValidationError(msg, detail={"product_id": key})

        subtotal += gross
        discount += line_discount
        tax += line_tax

    total = subtotal - discount + tax
    if subtotal > MAX_MONEY or total > MAX_MONEY:
        msg = "The invoice total is too large."
        raise InvoiceValidationError(
            msg,
            detail={"subtotal": str(subtotal), "total": str(total)},
        )

    declared = {
        "subtotal": _money(snapshot.subtotal),
        "discount_amount": _money(snapshot.discount_amount),
        "tax_amount": _money(snapshot.tax_amount),
        "total": _money(snapshot.total),
    }
    computed = {
        "subtotal": subtotal,
        "discount_amount": discount,
        "tax_amount": tax,
        "total": total,
    }
    if declared != computed:
        msg = "The sale totals do not match its lines."
        raise InvoiceValidationError(
            msg,
            detail={
                "sale_id": str(snapshot.sale_id),
                "declared": {k: str(v) for k, v in declared.items()},
                "computed": {k: str(v) for k, v in computed.items()},
            },
        )
    return _Totals(subtotal=subtotal, discount=discount, tax=tax, total=total)


def _assert_sale_references(
    organization: Organization,
    snapshot: SaleSnapshot,
) -> None:
    """Every product and variant on the sale must belong to this organization."""
    product_ids = {line.product_id for line in snapshot.lines}
    variant_ids = {line.variant_id for line in snapshot.lines if line.variant_id}
    try:
        found_products = (
            _model("products", "Product")
            .objects.filter(organization=organization, pk__in=product_ids)
            .count()
        )
        found_variants = (
            _model("products", "ProductVariant")
            .objects.filter(product__organization=organization, pk__in=variant_ids)
            .count()
            if variant_ids
            else 0
        )
    except _BAD_ID_ERRORS as exc:
        msg = "A product or variant id on the sale is malformed."
        raise InvoiceValidationError(msg, detail={"sale_id": str(snapshot.sale_id)}) from exc
    if found_products != len(product_ids) or found_variants != len(variant_ids):
        msg = "The sale references products that do not belong to this organization."
        raise InvoiceValidationError(msg, detail={"sale_id": str(snapshot.sale_id)})


# --------------------------------------------------------------------------- #
# Issue for a sale
# --------------------------------------------------------------------------- #


@transaction.atomic
def issue_invoice_for_sale(
    organization: Organization,
    sale: Any,
    *,
    actor: Any,
    upfront_payment: Decimal | int | str = 0,
    due_date: date | None = None,
    credit_approval_id: Any = None,
    audit_context: AuditContext | None = None,
) -> Invoice:
    """
    Issue the invoice for a sale. Called by the sales app inside its own
    transaction, after the sale and its stock movement are written.

    The sale owns the order and the stock; this function never moves stock and
    never writes `amount_paid`. The invoice is born Confirmed, with its number
    allocated here so a failure anywhere rolls the number back with the whole
    sale. Lines and totals are snapshotted from the sale and cross-checked: the
    sale header must equal its lines. The customer's details and addresses are
    frozen on the invoice.

    `upfront_payment` is the amount the caller records against this invoice
    through the payments service, in the same transaction and right after this
    call (PRD §42). It only reduces the credit exposure checked here; the
    payments service moves the invoice to Partially Paid or Paid.

    The due date is `due_date`, else the sale's, else the customer's payment
    terms, counted from the organization's own today. A sale can have only one
    live invoice: a second call raises InvoiceAlreadyIssuedForSaleError,
    enforced by the database as well as by an up-front check. A customer over
    their credit limit raises CreditLimitExceededError unless approved; callers
    must let both propagate so the sale rolls back.

    Lock order is invoice -> customer -> inventory; the sales app is expected to
    have locked the customer before touching inventory.
    """
    ports = get_invoice_ports()
    ports.authorize(actor, organization, PERM_INVOICE_CREATE)

    snapshot = ports.get_sale_snapshot(sale)
    totals = _validate_sale_snapshot(organization, snapshot)
    paid_now = _validate_upfront_payment(upfront_payment, totals.total)
    _assert_sale_references(organization, snapshot)

    already_issued = (
        Invoice.objects.for_organization(organization)
        .filter(sale_id=snapshot.sale_id)
        .not_voided()
        .exists()
    )
    if already_issued:
        raise InvoiceAlreadyIssuedForSaleError(detail={"sale_id": str(snapshot.sale_id)})

    # The status is checked on the locked row, not on an earlier read.
    customer = load_active_customer(organization, snapshot.customer_id, lock=True)
    warehouse = _load_warehouse(organization, snapshot.warehouse_id)

    issue_date = _today(organization)
    due = _resolve_due_date(customer, issue_date, due_date or snapshot.due_date)
    customer_details = customer_snapshot(
        customer,
        billing_address=getattr(snapshot, "billing_address", None),
        shipping_address=getattr(snapshot, "shipping_address", None),
    )

    number = allocate_document_number(organization, TEMPLATE_TYPES.INVOICE)
    # Balance before this invoice exists, so the credit check below is exact.
    _sync_balance(customer)

    try:
        with transaction.atomic():
            invoice = Invoice.objects.create(
                organization=organization,
                customer=customer,
                sale_id=snapshot.sale_id,
                warehouse=warehouse,
                sales_rep_id=snapshot.sales_rep_id,
                confirmed_by=actor,
                invoice_number=number.formatted,
                due_date=due,
                status=_initial_status(totals.total),
                subtotal=totals.subtotal,
                discount_amount=totals.discount,
                tax_amount=totals.tax,
                total=totals.total,
                issued_at=timezone.now(),
                notes=(snapshot.notes or "").strip() or None,
                **customer_details,
            )
    except IntegrityError as exc:
        if _LIVE_INVOICE_CONSTRAINT in str(exc):
            raise InvoiceAlreadyIssuedForSaleError(
                detail={"sale_id": str(snapshot.sale_id)}
            ) from exc
        raise

    InvoiceItem.objects.bulk_create(
        [
            InvoiceItem(
                invoice=invoice,
                product_id=line.product_id,
                variant_id=line.variant_id,
                description=(line.description or "").strip()[:255] or None,
                quantity=line.quantity,
                unit_name=line.unit_name,
                unit_multiplier=_decimal(
                    line.unit_multiplier, MULTIPLIER_STEP, field="unit multiplier"
                ),
                unit_price=_money(line.unit_price),
                discount_amount=_money(line.discount),
                tax_amount=_money(line.tax),
                tax_rate=_decimal(line.tax_rate, RATE_STEP, field="tax rate"),
            )
            for line in snapshot.lines
        ]
    )

    credit_override_used = _enforce_credit_limit(
        organization,
        actor,
        invoice,
        customer,
        invoice_total=totals.total,
        paid_now=paid_now,
        approval_id=credit_approval_id,
    )
    _sync_balance(customer)

    record(
        action=AuditAction.INVOICE_CREATED,
        organization=organization,
        actor=actor,
        resource=invoice,
        new_values=_snapshot(invoice),
        approval_id=credit_approval_id if credit_override_used else None,
        metadata={
            "sale_id": str(snapshot.sale_id),
            "customer_id": str(customer.pk),
            "warehouse_id": str(warehouse.pk),
            "lines": len(snapshot.lines),
            "upfront_payment": str(paid_now),
            "credit_override": credit_override_used,
        },
        context=audit_context,
    )
    transaction.on_commit(partial(ports.publish, EVENT_INVOICE_CONFIRMED, invoice))
    logger.info(
        "invoice.issued_for_sale",
        extra={
            "organization_id": str(organization.pk),
            "invoice_id": str(invoice.pk),
            "sale_id": str(snapshot.sale_id),
        },
    )
    return invoice


# --------------------------------------------------------------------------- #
# Create and edit (standalone invoices)
# --------------------------------------------------------------------------- #


def _find_by_idempotency_key(organization: Organization, key: str) -> Invoice | None:
    return Invoice.objects.for_organization(organization).filter(idempotency_key=key).first()


@transaction.atomic
def create_invoice(
    organization: Organization,
    *,
    actor: Any,
    customer_id: Any,
    warehouse_id: Any,
    lines: Sequence[InvoiceLineInput],
    due_date: date | None = None,
    discount_amount: Decimal | int | str = 0,
    discount_approval_id: Any = None,
    notes: str | None = "",
    extra_data: dict[str, Any] | None = None,
    replaces: Invoice | None = None,
    idempotency_key: str | None = None,
    audit_context: AuditContext | None = None,
) -> Invoice:
    """
    Create a standalone Draft invoice (no sale). No stock moves yet: stock is
    validated and deducted at confirmation (PRD §16, §22).

    The invoice number is allocated here, inside the same transaction, so a
    failed creation never burns a number. Unit prices and line text are
    snapshotted from the product now and are never re-priced. The discount and
    tax are shared across the lines so every line carries its own amounts. The
    due date defaults to the customer's payment terms, counted from the
    organization's own today. Customer details are stored for display and
    refreshed when the invoice is confirmed.

    `idempotency_key` makes a retried request safe (PRD §45): the same key in
    the same organization returns the invoice created the first time instead of
    a second draft. Keys are not compared against the payload, so a client must
    use a fresh key for a different request.

    `replaces` must be a voided invoice of this organization (PRD §24).
    """
    ports = get_invoice_ports()
    ports.authorize(actor, organization, PERM_INVOICE_CREATE)

    key = _clean_idempotency_key(idempotency_key)
    if key is not None:
        existing = _find_by_idempotency_key(organization, key)
        if existing is not None:
            return existing

    if replaces is not None:
        if replaces.organization_id != organization.pk:
            msg = "The replaced invoice belongs to another organization."
            raise InvoiceValidationError(msg, detail={"replaces": str(replaces.pk)})
        if replaces.status != _S.CANCELLED:
            msg = "Only a voided invoice can be replaced."
            raise InvoiceValidationError(msg, detail={"replaces": str(replaces.pk)})

    customer = load_active_customer(organization, customer_id)
    warehouse = _load_warehouse(organization, warehouse_id)
    due = _resolve_due_date(customer, _today(organization), due_date)
    items = _price_lines(organization, lines)
    totals = _calculate_totals(
        organization,
        actor,
        items,
        discount_amount,
        discount_approval_id,
    )
    amounts = _allocate_line_amounts(items, totals)

    try:
        # The number and the invoice share one savepoint: if a concurrent twin
        # request wins the idempotency race, the number is rolled back too.
        with transaction.atomic():
            number = allocate_document_number(organization, TEMPLATE_TYPES.INVOICE)
            invoice = Invoice.objects.create(
                organization=organization,
                customer=customer,
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
                idempotency_key=key,
                **customer_snapshot(customer),
            )
    except IntegrityError as exc:
        if key is not None and _IDEMPOTENCY_CONSTRAINT in str(exc):
            existing = _find_by_idempotency_key(organization, key)
            if existing is not None:
                return existing
        raise

    InvoiceItem.objects.bulk_create(_build_items(invoice, items, amounts))
    record(
        action=AuditAction.INVOICE_CREATED,
        organization=organization,
        actor=actor,
        resource=invoice,
        new_values=_snapshot(invoice),
        approval_id=discount_approval_id if totals.discount else None,
        metadata={
            "customer_id": str(customer.pk),
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


@transaction.atomic
def update_draft_invoice(
    organization: Organization,
    invoice_id: Any,
    *,
    actor: Any,
    lines: Sequence[InvoiceLineInput],
    due_date: date | None = None,
    discount_amount: Decimal | int | str | None = None,
    discount_approval_id: Any = None,
    notes: str | None = None,
    audit_context: AuditContext | None = None,
) -> Invoice:
    """
    Replace the lines of a standalone Draft and re-price it (PRD §24: only
    completed invoices are locked). `lines` is the full new set. The discount,
    due date and notes keep their current value when not given. The customer
    and warehouse cannot change: void and create a new invoice for that.

    Nothing else is editable: once confirmed, an invoice changes only through
    void and correct.
    """
    ports = get_invoice_ports()
    ports.authorize(actor, organization, PERM_INVOICE_CREATE)

    invoice = _lock_invoice(organization, invoice_id)
    _require_status(invoice, (_S.DRAFT,), "edited")
    if invoice.is_sale_origin:
        msg = "An invoice issued for a sale cannot be edited."
        raise InvalidInvoiceTransitionError(msg, detail={"invoice_id": str(invoice.pk)})

    before = _snapshot(invoice)
    customer = load_active_customer(organization, invoice.customer_id)
    due = (
        invoice.due_date
        if due_date is None
        else _resolve_due_date(customer, _today(organization), due_date)
    )
    items = _price_lines(organization, lines)
    totals = _calculate_totals(
        organization,
        actor,
        items,
        invoice.discount_amount if discount_amount is None else discount_amount,
        discount_approval_id,
    )
    amounts = _allocate_line_amounts(items, totals)

    InvoiceItem.objects.for_invoice(invoice).delete()
    InvoiceItem.objects.bulk_create(_build_items(invoice, items, amounts))

    invoice.subtotal = totals.subtotal
    invoice.discount_amount = totals.discount
    invoice.tax_amount = totals.tax
    invoice.total = totals.total
    invoice.due_date = due
    if notes is not None:
        invoice.notes = notes.strip() or None
    invoice.save(
        update_fields=[
            "subtotal",
            "discount_amount",
            "tax_amount",
            "total",
            "due_date",
            "notes",
            "updated_at",
        ]
    )
    record(
        action=AuditAction.INVOICE_UPDATED,
        organization=organization,
        actor=actor,
        resource=invoice,
        previous_values=before,
        new_values=_snapshot(invoice),
        approval_id=discount_approval_id if totals.discount else None,
        metadata={"lines": len(items)},
        context=audit_context,
    )
    logger.info(
        "invoice.updated",
        extra={"organization_id": str(organization.pk), "invoice_id": str(invoice.pk)},
    )
    return invoice


# --------------------------------------------------------------------------- #
# Confirm (standalone invoices)
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
    Confirm a standalone invoice atomically: lock the invoice, then the
    customer, re-check the customer is still active, validate credit, deduct
    stock through the ledger, freeze totals and the customer snapshot, refresh
    the customer balance, audit. Any failure, including INSUFFICIENT_STOCK,
    rolls back everything (PRD §22, §42).

    Sale-origin invoices are born confirmed and never pass through here; the
    sale has already moved their stock.

    Confirming an invoice that is already confirmed raises
    InvalidInvoiceTransitionError, so a double submit can never deduct stock
    twice. A draft whose due date has already passed (in the organization's own
    calendar) gets a fresh due date from the customer's payment terms instead
    of being born overdue; the change is in the audit record.

    `upfront_payment` is the amount the caller records against this invoice
    through the payments service in the same transaction. It only reduces the
    credit exposure checked here; this function never writes `amount_paid`.
    """
    ports = get_invoice_ports()
    ports.authorize(actor, organization, PERM_INVOICE_CONFIRM)

    invoice = _lock_invoice(organization, invoice_id)
    _require_status(invoice, CONFIRMABLE_STATUSES, "confirmed")
    if invoice.is_sale_origin:
        msg = "An invoice issued for a sale is already confirmed with its sale."
        raise InvalidInvoiceTransitionError(msg, detail={"invoice_id": str(invoice.pk)})

    rows = _load_lines(invoice)
    if not rows:
        msg = "An invoice without lines cannot be confirmed."
        raise InvoiceValidationError(msg, detail={"invoice_id": str(invoice.pk)})
    _assert_lines_match_header(invoice, rows)
    stock_lines = _stock_lines_from(rows)

    total = invoice.subtotal - invoice.discount_amount + invoice.tax_amount
    paid_now = _validate_upfront_payment(upfront_payment, total)

    # The status is checked on the locked row, not on the draft-time read.
    customer = load_active_customer(organization, invoice.customer_id, lock=True)

    issue_date = _today(organization)
    due = (
        invoice.due_date
        if invoice.due_date >= issue_date
        else _resolve_due_date(customer, issue_date, None)
    )
    due_date_renewed = due != invoice.due_date

    _sync_balance(customer)
    credit_override_used = _enforce_credit_limit(
        organization,
        actor,
        invoice,
        customer,
        invoice_total=total,
        paid_now=paid_now,
        approval_id=credit_approval_id,
    )

    before = _snapshot(invoice)
    ports.deduct_stock(invoice, stock_lines)

    invoice.status = _initial_status(total)
    invoice.confirmed_by = actor
    invoice.issued_at = timezone.now()
    invoice.due_date = due
    # Frozen from here on.
    invoice.total = total
    for field, value in customer_snapshot(customer).items():
        setattr(invoice, field, value)
    invoice.save(
        update_fields=[
            "status",
            "confirmed_by",
            "issued_at",
            "due_date",
            "total",
            *CUSTOMER_SNAPSHOT_FIELDS,
            "updated_at",
        ]
    )

    _sync_balance(customer)
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
            "upfront_payment": str(paid_now),
            "credit_override": credit_override_used,
            "due_date_renewed": due_date_renewed,
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
    A confirmed or overdue standalone invoice returns its stock through
    reversing ledger entries. A sale-origin invoice is voided through its
    sale: the sales app cancels the sale and reverses its stock exactly, in
    this same transaction. An invoice with payments is refused: reverse those
    first. Voiding does not require the customer to be active, so a blocked
    customer's mistaken invoice can still be cancelled.

    The approval port decides whether this void needs an approval and, if so,
    verifies and consumes `approval_id` (PRD §30). Lock order is
    invoice -> customer -> inventory.
    """
    ports = get_invoice_ports()
    ports.authorize(actor, organization, PERM_INVOICE_VOID)

    reason = (reason or "").strip()
    if len(reason) < MIN_VOID_REASON_LENGTH:
        msg = f"A reason of at least {MIN_VOID_REASON_LENGTH} characters is required."
        raise InvoiceValidationError(msg, detail={"reason": reason})

    invoice = _lock_invoice(organization, invoice_id)
    if invoice.status in PAID_STATUSES or invoice.amount_paid > 0:
        raise InvoiceHasPaymentsError(
            detail={"invoice_id": str(invoice.pk), "status": str(invoice.status)}
        )
    _require_status(invoice, VOIDABLE_STATUSES, "voided")

    stock_was_moved = invoice.status in VOIDABLE_CONFIRMED_STATUSES
    customer = (
        _fetch_customer(organization, invoice.customer_id, lock=True)
        if stock_was_moved
        else None
    )
    if stock_was_moved and ports.has_payments(invoice):
        raise InvoiceHasPaymentsError(detail={"invoice_id": str(invoice.pk)})

    ports.require_approval(
        actor=actor,
        organization=organization,
        action=APPROVAL_INVOICE_VOID,
        resource=invoice,
        approval_id=approval_id,
    )

    before = _snapshot(invoice)
    sale_cancelled = False
    if stock_was_moved:
        if invoice.sale_id is not None:
            ports.cancel_sale(invoice, actor=actor, reason=reason)
            sale_cancelled = True
        else:
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

    if customer is not None:
        _sync_balance(customer)
    record(
        action=AuditAction.INVOICE_VOIDED,
        organization=organization,
        actor=actor,
        resource=invoice,
        previous_values=before,
        new_values=_snapshot(invoice),
        reason=reason,
        approval_id=approval_id,
        metadata={
            "stock_restored": stock_was_moved,
            "sale_cancelled": sale_cancelled,
        },
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
    The PRD's correction flow for standalone invoices: Original Invoice >
    Void > Corrected Invoice (§24). Both steps share one transaction, so there
    is never a voided original without its replacement. The corrected invoice
    is a Draft linked back through `replaces`; confirm it like any other.

    A sale-origin invoice cannot be corrected here: the sale owns the order,
    so the fix is to void the invoice (which cancels the sale) and create a
    new sale.

    The actor needs both the void and the create permission, and the customer
    must still be active, otherwise nothing happens. The corrected invoice
    keeps the original's due date while it is still in the future; otherwise
    (or when `due_date` is not given and it has passed) it falls back to the
    customer's payment terms. `approval_id` covers the void;
    `discount_approval_id` covers any discount on the corrected invoice.
    """
    if _is_sale_origin(organization, invoice_id):
        msg = "An invoice issued for a sale cannot be corrected here. Void it and create a new sale."
        raise InvoiceValidationError(msg, detail={"invoice_id": str(invoice_id)})

    original = void_invoice(
        organization,
        invoice_id,
        actor=actor,
        reason=reason,
        approval_id=approval_id,
        audit_context=audit_context,
    )
    if due_date is None and original.due_date >= _today(organization):
        due_date = original.due_date
    return create_invoice(
        organization,
        actor=actor,
        customer_id=original.customer_id,
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


def _sweep_organization(organization: Organization, today: date, batch_size: int) -> int:
    """
    Mark one tenant's past-due Confirmed invoices Overdue, in short bounded
    transactions. Rows locked by a concurrent confirm or void are skipped and
    picked up on the next run instead of making the sweep wait.
    """
    ports = get_invoice_ports()
    swept = 0
    while True:
        with transaction.atomic():
            batch = list(
                Invoice.objects.filter(
                    organization=organization,
                    status=_S.CONFIRMED,
                    due_date__lt=today,
                )
                .order_by("pk")
                .select_for_update(skip_locked=True)[:batch_size]
            )
            if not batch:
                break
            Invoice.objects.filter(pk__in=[i.pk for i in batch]).update(
                status=_S.OVERDUE,
                updated_at=timezone.now(),
            )
            for invoice in batch:
                invoice.status = _S.OVERDUE
                transaction.on_commit(partial(ports.publish, EVENT_INVOICE_OVERDUE, invoice))
            swept += len(batch)
    return swept


def mark_overdue_invoices(
    *,
    today: date | None = None,
    batch_size: int = OVERDUE_BATCH_SIZE,
) -> int:
    """
    Move Confirmed invoices past their due date to Overdue. Schedule it hourly,
    because "past due" is decided on each organization's own calendar (PRD §7):
    a tenant's invoices turn overdue when its own midnight passes, whatever the
    server's timezone.

    Cross-tenant by design (a system job), but it works one tenant at a time in
    short bounded transactions, so it never holds long locks or loads the table
    into memory. Partially paid invoices keep their status;
    `InvoiceQuerySet.overdue()` still finds them by due date. Each invoice
    moved publishes EVENT_INVOICE_OVERDUE after commit, which is where payment
    reminders (PRD §33, §51) hook in.

    Pass `today` to force one date for every tenant (tests, backfills).
    """
    # Nobody's calendar is more than a day away from UTC's, so an invoice due
    # after this horizon cannot be overdue anywhere yet.
    horizon = timezone.now().date() + timedelta(days=2)
    organization_ids = list(
        Invoice.objects.filter(status=_S.CONFIRMED, due_date__lt=horizon)
        .order_by()
        .values_list("organization_id", flat=True)
        .distinct()
    )
    updated = 0
    if organization_ids:
        organizations = _model("users", "Organization").objects.filter(pk__in=organization_ids)
        for organization in organizations.iterator():
            updated += _sweep_organization(
                organization,
                today or _today(organization),
                batch_size,
            )
    logger.info(
        "invoice.overdue_sweep",
        extra={
            "updated": updated,
            "organizations": len(organization_ids),
            "as_of": today.isoformat() if today else None,
        },
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
    return list(qs.for_list().page_after(cursor, max(1, min(limit, MAX_PAGE_SIZE))))


def get_invoice_summary(
    organization: Organization,
    *,
    actor: Any,
    filters: InvoiceFilters | None = None,
) -> dict[str, Any]:
    """
    The Summary block of the invoice list page (PRD §38). The money totals
    (count, subtotal, discount, tax, total, paid, balance due) cover only
    invoices that count as real sales: confirmed and beyond. Drafts, pending
    and voided invoices never inflate them. They are reported separately as
    `draft_count` (drafts and pending) and `voided_count`. Two queries.
    """
    get_invoice_ports().authorize(actor, organization, PERM_INVOICE_VIEW)
    matching = _filtered_invoices(organization, filters or InvoiceFilters())
    summary = matching.confirmed_sales().totals()
    summary.update(matching.pipeline_counts())
    return summary


def available_actions(invoice: Invoice, permissions: Collection[str]) -> list[str]:
    """
    Actions the holder of `permissions` may attempt on this invoice, so the
    frontend never re-implements status rules (PRD §39). Advisory only: every
    action is re-validated when it runs (PRD §29).

    Sale-origin invoices are born confirmed and are corrected through their
    sale, so they offer void but never edit, confirm or void-and-correct.
    """
    if invoice.is_voided:
        return []
    sale_origin = invoice.is_sale_origin
    actions: list[str] = []
    if not sale_origin and invoice.status == _S.DRAFT and PERM_INVOICE_CREATE in permissions:
        actions.append(ACTION_EDIT)
    if (
        not sale_origin
        and invoice.status in CONFIRMABLE_STATUSES
        and PERM_INVOICE_CONFIRM in permissions
    ):
        actions.append(ACTION_CONFIRM)
    if invoice.status in VOIDABLE_STATUSES and PERM_INVOICE_VOID in permissions:
        actions.append(ACTION_VOID)
        if not sale_origin and PERM_INVOICE_CREATE in permissions:
            actions.append(ACTION_VOID_AND_CORRECT)
    return actions


def _filtered_invoices(organization: Organization, f: InvoiceFilters):
    """
    The tenant's invoices narrowed by `f`. A malformed id matches nothing.
    Issue-date filters and "overdue" use the organization's own calendar.
    """
    qs = Invoice.objects.for_organization(organization)
    try:
        if f.customer_id is not None:
            qs = qs.filter(customer_id=f.customer_id)
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
    qs = qs.issued_between(
        f.issued_from,
        f.issued_to,
        tz=_organization_tz(organization),
    ).due_between(f.due_from, f.due_to)
    if f.overdue_only:
        qs = qs.overdue(_today(organization))
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
