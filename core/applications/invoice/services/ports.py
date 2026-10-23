from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from typing import Any
from typing import Final
from typing import Protocol
from typing import runtime_checkable

from django.core.exceptions import ImproperlyConfigured

from core.applications.users.defaults import P

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date
    from decimal import Decimal

    from core.applications.customers.models import Customer
    from core.applications.invoice.models import Invoice
    from core.applications.users.models import Organization

# Permission codes passed to `InvoicePorts.authorize`.
PERM_INVOICE_VIEW: Final = P.VIEW_INVOICES.value
PERM_INVOICE_CREATE: Final = P.CREATE_SALE.value
PERM_INVOICE_CONFIRM: Final = P.CONFIRM_INVOICE.value
PERM_INVOICE_VOID: Final = P.CANCEL_INVOICE.value
PERM_AUDIT_VIEW: Final = P.VIEW_AUDIT_LOGS.value

# Actions that may need an approval, passed to `InvoicePorts.require_approval`.
APPROVAL_INVOICE_VOID: Final = "invoice.void"
APPROVAL_CREDIT_OVERRIDE: Final = "invoice.credit_override"

# Domain events passed to `InvoicePorts.publish` after the transaction commits.
EVENT_INVOICE_CONFIRMED: Final = "invoice.confirmed"
EVENT_INVOICE_VOIDED: Final = "invoice.voided"
EVENT_INVOICE_OVERDUE: Final = "invoice.overdue"


@dataclass(frozen=True, slots=True)
class PricedLine:
    """
    A line after the backend has priced it
    (PRD §39: the frontend never sends prices).
    """

    product_id: Any
    quantity: int
    unit_price: Decimal


@dataclass(frozen=True, slots=True)
class UnitResolution:
    unit_name: str          # a UnitOfMeasureChoices value
    multiplier: Decimal     # base units in one of this unit
    unit_price: Decimal     # price per ONE of this unit

@dataclass(frozen=True, slots=True)
class StockLine:
    """A quantity to deduct from, or return to, the invoice's warehouse."""

    product_id: Any
    quantity: int


@dataclass(frozen=True, slots=True)
class SaleLineSnapshot:
    """One line of a sale, exactly as the sales app priced it."""

    product_id: Any
    variant_id: Any
    description: str
    quantity: int
    unit_name: str
    unit_multiplier: Decimal
    unit_price: Decimal
    discount: Decimal
    tax: Decimal
    tax_rate: Decimal


@dataclass(frozen=True, slots=True)
class SaleSnapshot:
    """
    A read-only view of a sale, built by the adapter from the sales app's
    public API so the invoice app never imports sales models.

    `sale_id` is the Sale's primary key (not its human-readable `sale_id`
    code): it becomes `Invoice.sale`. `customer_id` is the sale's
    `customers.Customer`, the same record the invoice points at.

    `billing_address` and `shipping_address` are the addresses chosen on the
    sale. When empty, the invoice falls back to the customer's saved ones.
    """

    sale_id: Any
    organization_id: Any
    customer_id: Any
    warehouse_id: Any
    sales_rep_id: Any
    currency: str
    due_date: date | None
    notes: str | None
    is_cancelled: bool
    subtotal: Decimal
    discount_amount: Decimal
    tax_amount: Decimal
    total: Decimal
    lines: tuple[SaleLineSnapshot, ...]
    billing_address: str = ""
    shipping_address: str = ""


@runtime_checkable
class InvoicePorts(Protocol):
    def authorize(self, actor: Any, organization: Organization, permission: str) -> None:
        """Raise the platform's permission error unless `actor` holds `permission` in `organization`."""

    def validate_discount(
        self,
        *,
        actor: Any,
        organization: Organization,
        subtotal: Decimal,
        discount: Decimal,
        approval_id: Any = None,
    ) -> None:
        """
        Raise ApprovalRequiredError if the discount needs an approval that is
        missing or invalid (PRD §30).
        """

    def require_approval(
        self,
        *,
        actor: Any,
        organization: Organization,
        action: str,
        resource: Invoice,
        approval_id: Any,
    ) -> None:
        """
        Raise ApprovalRequiredError unless `action` may proceed: either the
        organization's rules do not require approval for it on `resource` (or
        `actor` holds an override permission), or `approval_id` names an
        Approved request that matches this organization, action and resource
        and is unexpired and unused. A valid approval is consumed here, inside
        the caller's transaction, so a rollback un-consumes it and it can
        never be replayed. Never trust the id without checking all of that.
        """

    def calculate_tax(
        self,
        *,
        organization: Organization,
        lines: Sequence[PricedLine],
        taxable_amount: Decimal,
    ) -> Decimal:
        """Tax for the invoice, per the organization's tax configuration (PRD §39)."""

    def deduct_stock(self, invoice: Invoice, lines: Sequence[StockLine]) -> None:
        """
        Standalone invoices only. Validate availability and write inventory
        ledger entries, in the caller's transaction. Raise
        InsufficientStockError if short. `lines` arrive sorted by product id:
        lock inventory rows in that order so concurrent confirmations cannot
        deadlock (PRD §43).
        """

    def restore_stock(self, invoice: Invoice, lines: Sequence[StockLine]) -> None:
        """Standalone invoices only. Write reversing ledger entries for a voided, previously confirmed invoice."""

    def get_sale_snapshot(self, sale: Any) -> SaleSnapshot:
        """
        Read `sale` through the sales app's public API into a snapshot. The
        snapshot's `sale_id` must be the Sale's primary key.
        """

    def cancel_sale(self, invoice: Invoice, *, actor: Any, reason: str) -> None:
        """
        Called when a sale-origin invoice is voided. The sales app cancels the
        sale and reverses its stock exactly (same batches), inside the
        caller's transaction. Must not call back into the invoice services.
        """

    def has_payments(self, invoice: Invoice) -> bool:
        """True if any payment has been recorded against the invoice."""

    def refresh_customer_balance(self, customer: Customer) -> None:
        """
        Recompute Customer.balance as the sum of (total - amount_paid) over the
        customer's invoices that are Confirmed, Overdue or Partially Paid.
        Drafts, Pending, Paid and Cancelled invoices contribute nothing, so an
        invoice being confirmed is not counted until it has been saved. Idempotent.
        Called with the customer row already locked, and it must update the
        passed instance too. This is the only writer of Customer.balance.
        """

    def publish(self, event: str, invoice: Invoice) -> None:
        """
        Called after commit. Enqueue PDF generation, emails and notifications
        to a background worker here; never do slow work inline (PRD §51).
        """

    def resolve_unit(
        self,
        *,
        organization: Organization,
        product: Any,
        variant: Any,
        unit_name: str,
        base_price: Decimal,
    ) -> UnitResolution:
        """
        Look the unit up in the product's unit conversions and return its
        multiplier and price. Raise InvoiceValidationError if the product has
        no such unit. `base_price` is the variant's or product's price per
        base unit; a conversion with its own price should return that instead.
        """

_registry: dict[str, InvoicePorts] = {}


def register_invoice_ports(ports: InvoicePorts) -> None:
    if not isinstance(ports, InvoicePorts):
        msg = "register_invoice_ports() needs an object implementing every InvoicePorts method."
        raise ImproperlyConfigured(msg)
    _registry["ports"] = ports


def get_invoice_ports() -> InvoicePorts:
    try:
        return _registry["ports"]
    except KeyError as exc:
        msg = "Invoice ports are not registered. Call register_invoice_ports() in AppConfig.ready()."
        raise ImproperlyConfigured(msg) from exc
