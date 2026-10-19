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
    from decimal import Decimal

    from core.applications.invoice.models import Client
    from core.applications.invoice.models import Invoice
    from core.applications.users.models import Organization

# Permission codes passed to `InvoicePorts.authorize`.
PERM_INVOICE_VIEW: Final = P.VIEW_INVOICES.value
PERM_INVOICE_CREATE: Final = P.CREATE_SALE.value
PERM_INVOICE_CONFIRM: Final = P.CONFIRM_INVOICE.value
PERM_INVOICE_VOID: Final = P.CANCEL_INVOICE.value
PERM_AUDIT_VIEW: Final = P.VIEW_AUDIT_LOGS.value

# Domain events passed to `InvoicePorts.publish` after the transaction commits.
EVENT_INVOICE_CONFIRMED: Final = "invoice.confirmed"
EVENT_INVOICE_VOIDED: Final = "invoice.voided"


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
class StockLine:
    """A quantity to deduct from, or return to, the invoice's warehouse."""

    product_id: Any
    quantity: int


@runtime_checkable
class InvoicePorts(Protocol):
    def authorize(self, actor: Any, organization: Organization, permission: str) -> None:
        """Raise the platform's permission error unless `actor` holds `permission` in `organization`."""

    def validate_discount(
        self, *, actor: Any, organization: Organization, subtotal: Decimal, discount: Decimal
    ) -> None:
        """Raise if the discount needs an approval that has not been granted (PRD §30)."""

    def calculate_tax(
        self, *, organization: Organization, lines: Sequence[PricedLine], taxable_amount: Decimal
    ) -> Decimal:
        """Tax for the invoice, per the organization's tax configuration (PRD §39)."""

    def deduct_stock(self, invoice: Invoice, lines: Sequence[StockLine]) -> None:
        """
        Validate availability and write inventory ledger entries, in the
        caller's transaction. Raise InsufficientStockError if short.
        `lines` arrive sorted by product id: lock inventory rows in that order
        so concurrent confirmations cannot deadlock (PRD §43).
        """

    def restore_stock(self, invoice: Invoice, lines: Sequence[StockLine]) -> None:
        """Write reversing ledger entries for a voided, previously confirmed invoice."""

    def has_payments(self, invoice: Invoice) -> bool:
        """True if any payment has been recorded against the invoice."""

    def refresh_client_balance(self, client: Client) -> None:
        """Recompute Client.current_balance from invoices and payments. Idempotent."""

    def publish(self, event: str, invoice: Invoice) -> None:
        """
        Called after commit. Enqueue PDF generation, emails and notifications
        to a background worker here; never do slow work inline (PRD §51).
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
