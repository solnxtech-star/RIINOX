from __future__ import annotations

import logging
import uuid
from decimal import ROUND_HALF_UP
from decimal import Decimal
from decimal import InvalidOperation
from typing import Any
from typing import Final

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from core.applications.customers.models import Customer
from core.applications.inventory.services import process_return_inventory_and_ledger
from core.applications.inventory.services import process_sale_inventory_and_ledger
from core.applications.invoice.exceptions import InvoiceValidationError
from core.applications.invoice.models import Invoice
from core.applications.invoice.services.invoices import allocate_amount
from core.applications.invoice.services.invoices import customer_snapshot
from core.applications.invoice.services.invoices import issue_invoice_for_sale
from core.applications.invoice.services.invoices import load_active_customer
from core.applications.invoice.services.ports import get_invoice_ports
from core.applications.notification.audit.action import AuditAction
from core.applications.notification.audit.services import record as audit_record
from core.applications.payment.services import (
    parse_payment,
    payment_status,
    record_invoice_payment,
    record_refund_for_return,
)
from core.applications.sales.models import Sale
from core.applications.sales.models import SaleItem
from core.applications.sales.models import SaleReturn
from core.applications.sales.models import SaleReturnItem
from core.applications.subscriptions.services.entitlements import (
    ensure_subscription_active,
)
from core.helper.enums import InvoiceStatusChoices
from core.helper.enums import SalePaymentStatusChoices

logger = logging.getLogger(__name__)

CENT: Final = Decimal("0.01")
RATE_STEP: Final = Decimal("0.01")
MAX_MONEY: Final = Decimal("9999999999.99")


class SaleValidationError(InvoiceValidationError):
    """A sale or return request that breaks a business rule. Shares the invoice
    error contract, so the API layer maps it to the same structured response."""


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _money(value: Any, *, field: str = "amount") -> Decimal:
    try:
        return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError) as exc:
        msg = f"Invalid {field}."
        raise SaleValidationError(msg, detail={field: str(value)}) from exc


def _reference(prefix: str, key: str) -> str:
    return f"{prefix}-{key}-{uuid.uuid4().hex[:6].upper()}"


def _unit_multiplier(product: Any, unit_name: str) -> Decimal:
    """
    How many base units one `unit_name` is. The product's own unit is 1; any
    other unit must have a configured conversion, otherwise the stock movement
    would be wrong, so the sale is refused instead of guessing 1.
    """
    if unit_name == product.unit_of_measurement:
        return Decimal("1.000")
    conversion = product.unit_conversions.filter(unit_name=unit_name).first()
    if conversion is None:
        msg = "This product has no conversion for the selected unit."
        raise SaleValidationError(
            msg,
            detail={"product_id": str(product.pk), "unit_name": unit_name},
        )
    return Decimal(str(conversion.multiplier))


# --------------------------------------------------------------------------- #
# Building the sale lines (PRD §39: the backend calculates, never the caller)
# --------------------------------------------------------------------------- #


def _build_lines(
    organization: Any,
    items_data: list[dict[str, Any]],
    header_discount: Any,
    header_tax: Any,
) -> tuple[list[dict[str, Any]], dict[str, Decimal], int]:
    """
    Turn the request lines into SaleItem values and the sale totals.

    - Unit prices default to the product's sale price. An explicit price is
      kept (customer pricing) and counted so the audit shows price overrides.
    - A sale-level discount or tax is shared across the lines in whole cents, so
      every line carries its own amounts and the lines add up exactly to the
      header, which is what the invoice services verify. Per-line discount/tax
      on the request are honoured instead, but not both for the same amount.
    - `subtotal` and `total` from the caller are ignored and recomputed.

    Returns (item kwargs, totals, number of price overrides).
    """
    if not items_data:
        msg = "A sale needs at least one line."
        raise SaleValidationError(msg, detail={"items": []})

    lines: list[dict[str, Any]] = []
    overrides = 0
    for raw in items_data:
        product = raw["product"]
        variant = raw.get("variant")
        if product.organization_id != organization.pk:
            msg = "A product belongs to another organization."
            raise SaleValidationError(msg, detail={"product_id": str(product.pk)})
        if variant is not None and variant.product_id != product.pk:
            msg = "The variant does not belong to the product."
            raise SaleValidationError(msg, detail={"variant_id": str(variant.pk)})

        quantity = raw["quantity"]
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 1:
            msg = "Quantity must be a whole number of at least 1."
            raise SaleValidationError(
                msg, detail={"product_id": str(product.pk), "quantity": str(quantity)}
            )

        catalog_price = product.customer_sale_price
        price_given = raw.get("unit_price")
        unit_price = _money(catalog_price if price_given is None else price_given, field="unit price")
        if unit_price < 0:
            msg = "A unit price cannot be negative."
            raise SaleValidationError(msg, detail={"product_id": str(product.pk)})
        if catalog_price is not None and unit_price != _money(catalog_price):
            overrides += 1

        unit_name = raw.get("unit_name") or product.unit_of_measurement
        lines.append(
            {
                "product": product,
                "variant": variant,
                "product_name": raw.get("product_name") or product.name,
                "product_sku": getattr(product, "sku", None),
                "quantity": quantity,
                "unit_name": unit_name,
                "unit_multiplier": _unit_multiplier(product, unit_name),
                "unit_price": unit_price,
                "discount": _money(raw.get("discount") or 0, field="discount"),
                "tax": _money(raw.get("tax") or 0, field="tax"),
                "tax_rate": _money(raw.get("tax_rate") or 0, field="tax rate"),
            }
        )

    gross = [line["unit_price"] * line["quantity"] for line in lines]
    discount = _money(header_discount or 0, field="discount")
    tax = _money(header_tax or 0, field="tax")
    if discount < 0 or tax < 0:
        msg = "Discount and tax cannot be negative."
        raise SaleValidationError(msg, detail={"discount": str(discount), "tax": str(tax)})

    if discount:
        if any(line["discount"] for line in lines):
            msg = "Give the discount on the sale or on its lines, not both."
            raise SaleValidationError(msg, detail={"discount": str(discount)})
        if discount > sum(gross, Decimal(0)):
            msg = "The discount cannot exceed the subtotal."
            raise SaleValidationError(msg, detail={"discount": str(discount)})
        for line, share in zip(lines, allocate_amount(discount, gross), strict=True):
            line["discount"] = share

    for line, line_gross in zip(lines, gross, strict=True):
        if line["discount"] > line_gross:
            msg = "A line discount cannot exceed the line value."
            raise SaleValidationError(msg, detail={"product_id": str(line["product"].pk)})

    taxable = [g - line["discount"] for line, g in zip(lines, gross, strict=True)]
    if tax:
        if any(line["tax"] for line in lines):
            msg = "Give the tax on the sale or on its lines, not both."
            raise SaleValidationError(msg, detail={"tax": str(tax)})
        for line, share in zip(lines, allocate_amount(tax, taxable), strict=True):
            line["tax"] = share

    for line, base in zip(lines, taxable, strict=True):
        if tax and base > 0:
            # Effective rate of the share this line received.
            line["tax_rate"] = min(
                (line["tax"] / base * 100).quantize(RATE_STEP, rounding=ROUND_HALF_UP),
                Decimal(100),
            )
        if not Decimal(0) <= line["tax_rate"] <= Decimal(100):
            msg = "A line has an invalid tax rate."
            raise SaleValidationError(msg, detail={"product_id": str(line["product"].pk)})

    subtotal = sum(gross, Decimal(0))
    total_discount = sum((line["discount"] for line in lines), Decimal(0))
    total_tax = sum((line["tax"] for line in lines), Decimal(0))
    total = subtotal - total_discount + total_tax
    if subtotal > MAX_MONEY or total > MAX_MONEY:
        msg = "The sale total is too large."
        raise SaleValidationError(msg, detail={"subtotal": str(subtotal), "total": str(total)})
    totals = {
        "subtotal": subtotal,
        "discount_amount": total_discount,
        "tax_amount": total_tax,
        "total": total,
    }
    return lines, totals, overrides





# --------------------------------------------------------------------------- #
# Audit
# --------------------------------------------------------------------------- #


def record_sale_audit_event(sale: Sale, **extra: Any) -> None:
    """Immutable audit event for sale creation (PRD §31)."""
    audit_record(
        action=AuditAction.SALES_ORDER_CREATED,
        organization=sale.organization,
        actor=sale.sales_rep,
        resource=sale,
        metadata={
            "sale_id": sale.sale_id,
            "total": str(sale.total),
            "customer_id": str(sale.customer_id),
            "payment_status": sale.payment_status,
            "fulfillment_status": sale.fulfillment_status,
            "items_count": sale.items.count(),
            **extra,
        },
    )


def record_sale_return_audit_event(sale_return: SaleReturn) -> None:
    """Immutable audit event for a sale return (PRD §23, §31)."""
    audit_record(
        action=AuditAction.SALES_RETURN_CREATED,
        organization=sale_return.organization,
        actor=sale_return.original_sale.sales_rep,
        resource=sale_return,
        metadata={
            "sale_return_id": str(sale_return.id),
            "original_sale_id": str(sale_return.original_sale_id),
            "refund_amount": str(sale_return.refund_amount),
            "refund_method": sale_return.refund_method,
            "return_reason": sale_return.return_reason,
            "items_count": sale_return.items.count(),
        },
    )


# --------------------------------------------------------------------------- #
# Create sale
# --------------------------------------------------------------------------- #


@transaction.atomic
def create_sale(data: dict[str, Any], *, actor: Any = None) -> Sale:
    """
    Create a sale and run its whole execution atomically (PRD §21, §42).

    `data` carries model instances like before (organization, customer,
    location, sales_rep, items with `product`), plus optional:

    - `payment`: {"amount", "method", "reference"} tendered now (PRD §39).
    - `due_date`: otherwise the customer's payment terms apply.
    - `credit_approval_id`: approval to exceed the customer's credit limit.

    Totals and payment status are computed here; `subtotal`, `total` and
    `payment_status` in `data` are ignored. Any failure (insufficient stock,
    credit limit, inactive customer, invalid payment) rolls the whole sale back.
    The caller's dict is not modified.
    """
    data = dict(data)
    organization = data.get("organization")
    if organization is None:
        msg = "A sale needs an organization."
        raise SaleValidationError(msg, detail={})
    ensure_subscription_active(organization)

    actor = actor or data.get("sales_rep")
    if actor is None:
        msg = "A sale must be attributed to a user."
        raise SaleValidationError(msg, detail={})
    data["sales_rep"] = data.get("sales_rep") or actor

    items_data = list(data.pop("items", []))
    payment_data = data.pop("payment", None)
    credit_approval_id = data.pop("credit_approval_id", None)

    # Customer first: its row lock must be taken before inventory is touched.
    customer = load_active_customer(organization, data["customer"].pk, lock=True)
    data["customer"] = customer
    location = data["location"]
    if location.warehouse.organization_id != organization.pk:
        msg = "The location belongs to another organization."
        raise SaleValidationError(msg, detail={"location_id": str(location.pk)})

    lines, totals, price_overrides = _build_lines(
        organization,
        items_data,
        data.pop("discount_amount", 0),
        data.pop("tax_amount", 0),
    )
    paid_now, method, payment_reference = parse_payment(payment_data, totals["total"])

    data.pop("subtotal", None)
    data.pop("total", None)
    data["payment_status"] = payment_status(totals["total"], paid_now)
    data["currency"] = data.get("currency") or getattr(organization, "currency", None) or "USD"
    if not data.get("sale_id"):
        data["sale_id"] = f"SALE-{uuid.uuid4().hex[:8].upper()}"
    # Keep the sale's addresses consistent with what the invoice will freeze.
    details = customer_snapshot(
        customer,
        billing_address=data.get("billing_address"),
        shipping_address=data.get("shipping_address"),
    )
    data["billing_address"] = details["billing_address"] or None
    data["shipping_address"] = details["shipping_address"] or None

    sale = Sale.objects.create(**data, **totals)
    SaleItem.objects.bulk_create([SaleItem(sale=sale, **line) for line in lines])

    process_sale_inventory_and_ledger(sale)
    invoice = issue_invoice_for_sale(
        organization,
        sale,
        actor=actor,
        upfront_payment=paid_now,
        due_date=data.get("due_date"),
        credit_approval_id=credit_approval_id,
    )

    payment_ref = None
    if paid_now > 0:
        payment, _receipt = record_invoice_payment(
            invoice,
            amount=paid_now,
            method=method,
            actor=actor,
            currency=sale.currency,
            reference=payment_reference or _reference("PAY", sale.sale_id),
            description=f"Payment for Sale {sale.sale_id}",
        )
        payment_ref = payment.reference

    record_sale_audit_event(
        sale,
        invoice_number=invoice.invoice_number,
        amount_paid=str(paid_now),
        payment_reference=payment_ref,
        price_overrides=price_overrides,
    )
    logger.info(
        "sale.created",
        extra={
            "organization_id": str(organization.pk),
            "sale_id": str(sale.pk),
            "invoice_id": str(invoice.pk),
        },
    )
    return sale


# --------------------------------------------------------------------------- #
# Returns (PRD §23)
# --------------------------------------------------------------------------- #


def _returnable_amount(item: SaleItem, quantity: int) -> Decimal:
    """The most that can be refunded for `quantity` of this line."""
    return (item.total * quantity / item.quantity).quantize(CENT, rounding=ROUND_HALF_UP)





@transaction.atomic
def create_sale_return(data: dict[str, Any]) -> SaleReturn:
    """
    Create a SaleReturn and its items, then apply the inventory effect and the
    refund (PRD §23). Quantities are checked against what the sale still has
    returnable, on locked sale-item rows, and each refund against its line.
    The caller's dict is not modified.
    """
    data = dict(data)
    organization = data.get("organization")
    if organization is not None:
        ensure_subscription_active(organization)

    original_sale: Sale = data["original_sale"]
    if organization is not None and original_sale.organization_id != organization.pk:
        msg = "The sale belongs to another organization."
        raise SaleValidationError(msg, detail={"sale_id": str(original_sale.pk)})

    items_data = list(data.pop("items", []))
    if not items_data:
        msg = "A return needs at least one item."
        raise SaleValidationError(msg, detail={"items": []})

    sale_items = {
        item.pk: item for item in SaleItem.objects.select_for_update().filter(sale=original_sale)
    }
    requested: dict[Any, int] = {}
    refund_total = Decimal("0.00")
    for item_data in items_data:
        sale_item = sale_items.get(item_data["sale_item"].pk)
        if sale_item is None:
            msg = "A returned item is not part of the original sale."
            raise SaleValidationError(msg, detail={"sale_item_id": str(item_data["sale_item"].pk)})
        quantity = item_data["quantity"]
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 1:
            msg = "A returned quantity must be a whole number of at least 1."
            raise SaleValidationError(msg, detail={"sale_item_id": str(sale_item.pk)})
        requested[sale_item.pk] = requested.get(sale_item.pk, 0) + quantity
        if requested[sale_item.pk] > sale_item.quantity - sale_item.returned_quantity:
            msg = "More is being returned than the sale still has returnable."
            raise SaleValidationError(
                msg,
                detail={
                    "sale_item_id": str(sale_item.pk),
                    "returnable": sale_item.quantity - sale_item.returned_quantity,
                },
            )
        refund = _money(item_data["refund_amount"], field="refund amount")
        if not Decimal(0) <= refund <= _returnable_amount(sale_item, quantity):
            msg = "A refund exceeds the value of the returned quantity."
            raise SaleValidationError(msg, detail={"sale_item_id": str(sale_item.pk)})
        refund_total += refund

    declared = data.get("refund_amount")
    if declared is not None and _money(declared, field="refund amount") != refund_total:
        msg = "The refund total does not match its items."
        raise SaleValidationError(
            msg, detail={"declared": str(declared), "computed": str(refund_total)}
        )
    data["refund_amount"] = refund_total

    sale_return = SaleReturn.objects.create(**data)
    for item_data in items_data:
        SaleReturnItem.objects.create(sale_return=sale_return, **item_data)

    process_return_inventory_and_ledger(sale_return)
    record_refund_for_return(sale_return)
    record_sale_return_audit_event(sale_return)
    return sale_return


# ---------------------------------------------------------------------------
# Backwards compatibility aliases (the invoice alias is gone: invoices are now
# issued by the invoice services, not built here).
# ---------------------------------------------------------------------------
mock_create_audit_event = record_sale_audit_event
