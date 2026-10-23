from __future__ import annotations

from decimal import Decimal
from typing import Final

from django.db import transaction
from django.db.models import F
from django.db.models import Sum
from django.utils import timezone

from core.applications.customers.models import Customer
from core.applications.inventory.services import reverse_sale_inventory_and_ledger
from core.applications.invoice.exceptions import InvoiceValidationError
from core.applications.invoice.models import Invoice
from core.applications.invoice.services.ports import SaleLineSnapshot
from core.applications.invoice.services.ports import SaleSnapshot
from core.applications.payment.models import InvoicePayment
from core.applications.sales.models import Sale
from core.helper.enums import InvoiceStatusChoices

# Invoices that still have money owing. Drafts, Pending, Paid and Cancelled
# invoices contribute nothing to a customer's balance.
OUTSTANDING_STATUSES: Final = (
    InvoiceStatusChoices.CONFIRMED,
    InvoiceStatusChoices.OVERDUE,
    InvoiceStatusChoices.PARTIALLY_PAID,
)


def build_sale_snapshot(sale: Sale) -> SaleSnapshot:
    """
    Read a sale into the invoice app's snapshot. `sale_id` is the Sale's primary
    key, which becomes `Invoice.sale`; the human-readable `Sale.sale_id` code is
    not used here. Line descriptions are the product names frozen on the sale.
    """
    items = sale.items.all().order_by("pk")
    return SaleSnapshot(
        sale_id=sale.pk,
        organization_id=sale.organization_id,
        customer_id=sale.customer_id,
        warehouse_id=(
            sale.warehouse_id
            or (sale.location.warehouse_id if sale.location else None)
        ),
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
def cancel_sale_for_invoice(invoice: Invoice, *, actor, reason: str) -> None:
    """
    Cancel the sale behind a voided sale-origin invoice and reverse its stock
    exactly (PRD §44: cancel, never delete). Runs inside the void's transaction
    and never calls the invoice services.
    """
    sale = Sale.objects.select_for_update().get(
        pk=invoice.sale_id,
        organization_id=invoice.organization_id,
    )
    if sale.cancelled_at is not None:
        msg = "The sale is already cancelled."
        raise InvoiceValidationError(msg, detail={"sale_id": str(sale.pk)})

    reverse_sale_inventory_and_ledger(sale, actor=actor, reason=reason)

    sale.cancelled_at = timezone.now()
    sale.cancelled_by = actor
    sale.cancel_reason = reason
    sale.save(update_fields=["cancelled_at", "cancelled_by", "cancel_reason", "updated_at"])


def invoice_has_payments(invoice: Invoice) -> bool:
    """True if any payment has been recorded against the invoice."""
    return InvoicePayment.objects.filter(invoice=invoice).exists()


def refresh_customer_balance(customer: Customer) -> None:
    """
    Recompute Customer.balance as total - amount_paid over the customer's
    Confirmed, Overdue and Partially Paid invoices. Idempotent. Call with the
    customer row locked. This is the only writer of Customer.balance, and it
    updates the passed instance as well as the row.
    """
    owed = (
        Invoice.objects.filter(
            organization_id=customer.organization_id,
            customer_id=customer.pk,
            status__in=OUTSTANDING_STATUSES,
        ).aggregate(owed=Sum(F("total") - F("amount_paid")))["owed"]
        or Decimal("0.00")
    )
    Customer.objects.filter(pk=customer.pk).update(balance=owed)
    customer.balance = owed
