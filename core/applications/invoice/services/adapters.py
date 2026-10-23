from __future__ import annotations

from decimal import Decimal
from typing import Any
from typing import Final

from django.db import transaction
from django.db.models import DecimalField
from django.db.models import ExpressionWrapper
from django.db.models import F
from django.db.models import Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from core.applications.customers.models import Customer
from core.applications.invoice.exceptions import InvoiceValidationError
from core.applications.invoice.models import Invoice
from core.applications.invoice.queryset import OUTSTANDING_STATUSES
from core.applications.invoice.services.ports import SaleLineSnapshot
from core.applications.invoice.services.ports import SaleSnapshot
from core.applications.payment.models import InvoicePayment
from core.applications.sales.models import Sale

CENT: Final = Decimal("0.01")
MONEY: Final = DecimalField(max_digits=14, decimal_places=2)


def build_sale_snapshot(sale: Sale) -> SaleSnapshot:
    """
    Read a sale into the invoice app's snapshot. `sale_id` is the Sale's primary
    key, which becomes `Invoice.sale`; the human-readable `Sale.sale_id` code is
    not used here. Line descriptions are the product names frozen on the sale.

    The warehouse comes from the sale's location. A sale without one has
    nowhere to supply goods from, so it cannot be invoiced.
    """
    if sale.location_id is None:
        msg = "The sale has no location, so it has no warehouse to supply it from."
        raise InvoiceValidationError(msg, detail={"sale_id": str(sale.pk)})

    items = sale.items.all().order_by("pk")
    return SaleSnapshot(
        sale_id=sale.pk,
        organization_id=sale.organization_id,
        customer_id=sale.customer_id,
        warehouse_id=sale.location.warehouse_id,
        sales_rep_id=sale.sales_rep_id,
        currency=sale.currency,
        due_date=sale.due_date,
        notes=sale.notes,
        is_cancelled=sale.cancelled_at is not None,
        subtotal=sale.subtotal,
        discount_amount=sale.discount_amount,
        tax_amount=sale.tax_amount,
        total=sale.total,
        lines=tuple(
            SaleLineSnapshot(
                product_id=item.product_id,
                variant_id=item.variant_id,
                description=item.product_name,
                quantity=item.quantity,
                unit_name=item.unit_name,
                unit_multiplier=item.unit_multiplier,
                unit_price=item.unit_price,
                discount=item.discount,
                tax=item.tax,
                tax_rate=item.tax_rate,
            )
            for item in items
        ),
        billing_address=sale.billing_address or "",
        shipping_address=sale.shipping_address or "",
    )


@transaction.atomic
def cancel_sale_for_invoice(invoice: Invoice, *, actor: Any, reason: str) -> None:
    """
    Cancel the sale behind a voided sale-origin invoice and reverse its stock
    exactly (PRD §44: cancel, never delete). Runs inside the void's transaction
    and never calls the invoice services.

    The sale row is locked first so two voids cannot reverse the same stock
    twice. The lock order stays invoice -> customer -> sale -> inventory.
    """
    # Imported here, not at module level: the inventory app is loaded while
    # AppConfig.ready() registers the invoice ports, so a top-level import
    # would crash startup (and risks an import cycle).
    # TODO: confirm this function exists in inventory/services.py, or point
    # this import at the function that already reverses a sale's stock.
    from core.applications.inventory.services import reverse_sale_inventory_and_ledger

    if invoice.sale_id is None:
        msg = "This invoice was not issued for a sale."
        raise InvoiceValidationError(msg, detail={"invoice_id": str(invoice.pk)})

    try:
        sale = Sale.objects.select_for_update().get(
            pk=invoice.sale_id,
            organization_id=invoice.organization_id,
        )
    except Sale.DoesNotExist as exc:
        msg = "The sale behind this invoice was not found."
        raise InvoiceValidationError(
            msg,
            detail={"invoice_id": str(invoice.pk), "sale_id": str(invoice.sale_id)},
        ) from exc

    if sale.cancelled_at is not None:
        msg = "The sale is already cancelled."
        raise InvoiceValidationError(msg, detail={"sale_id": str(sale.pk)})

    reverse_sale_inventory_and_ledger(sale, actor=actor, reason=reason)

    sale.cancelled_at = timezone.now()
    sale.cancelled_by = actor
    sale.cancel_reason = reason
    sale.save(update_fields=["cancelled_at", "cancelled_by", "cancel_reason", "updated_at"])


def invoice_has_payments(invoice: Invoice) -> bool:
    """
    True if any payment record is attached to the invoice. Deliberately
    conservative: a payment that was later refunded still blocks a void, so the
    money trail is reversed through the payments flow rather than hidden by a
    void.
    """
    return InvoicePayment.objects.filter(invoice=invoice).exists()


def refresh_customer_balance(customer: Customer) -> None:
    """
    Recompute Customer.balance as total - amount_paid over the customer's
    Confirmed, Overdue and Partially Paid invoices. Idempotent. Call with the
    customer row locked. This is the only writer of Customer.balance, and it
    updates the passed instance as well as the row.

    The set of invoice statuses that count is `OUTSTANDING_STATUSES` from the
    invoice querysets, so the balance and the "outstanding" reports can never
    disagree about what is owed.
    """
    owed = Invoice.objects.filter(
        organization_id=customer.organization_id,
        customer_id=customer.pk,
        status__in=OUTSTANDING_STATUSES,
    ).aggregate(
        owed=Coalesce(
            Sum(ExpressionWrapper(F("total") - F("amount_paid"), output_field=MONEY)),
            Decimal("0.00"),
            output_field=MONEY,
        )
    )["owed"].quantize(CENT)

    Customer.objects.filter(
        pk=customer.pk,
        organization_id=customer.organization_id,
    ).update(balance=owed)
    customer.balance = owed
