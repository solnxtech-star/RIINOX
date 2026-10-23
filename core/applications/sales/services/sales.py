from __future__ import annotations

import logging
import uuid
from decimal import Decimal
from typing import Any, Final

from django.db import transaction
from django.utils import timezone

from core.applications.inventory.services import (
    process_sale_inventory_and_ledger,
    reverse_sale_inventory_and_ledger,
)
from core.applications.invoice.services.invoices import (
    customer_snapshot,
    issue_invoice_for_sale,
    load_active_customer,
)
from core.applications.payment.services import (
    parse_payment,
    payment_status,
    record_invoice_payment,
)
from core.applications.products.services import get_unit_multiplier
from core.applications.sales.models import Sale, SaleItem
from core.applications.sales.services.audit import (
    record_sale_audit_event,
    record_sale_cancellation_audit_event,
)
from core.applications.sales.services.exceptions import (
    SaleCancellationError,
    SaleValidationError,
)
from core.applications.subscriptions.services.entitlements import (
    ensure_subscription_active,
)
from core.helper.enums import (
    SaleFulfillmentStatusChoices,
)
from core.helper.money import (
    CENT,
    MAX_MONEY,
    RATE_STEP,
    allocate_amount,
    quantize_money,
)
from core.helper.utils import generate_reference

logger = logging.getLogger(__name__)


def _money(value: Any, *, field: str = "amount") -> Decimal:
    """Safely parse and quantize a money value to whole cents."""
    try:
        return quantize_money(value, field=field)
    except ValueError as exc:
        raise SaleValidationError(f"Invalid {field}.", detail={field: str(value)}) from exc


_reference = generate_reference


def _unit_multiplier(product: Any, unit_name: str) -> Decimal:
    """
    How many base units one `unit_name` is. Queries the products service layer;
    raises SaleValidationError if unit conversion is missing.
    """
    try:
        return get_unit_multiplier(product, unit_name)
    except ValueError as exc:
        msg = "This product has no conversion for the selected unit."
        raise SaleValidationError(
            msg,
            detail={"product_id": str(getattr(product, "pk", product)), "unit_name": unit_name},
        ) from exc


def _build_lines(
    organization: Any,
    items_data: list[dict[str, Any]],
    header_discount: Any,
    header_tax: Any,
) -> tuple[list[dict[str, Any]], dict[str, Decimal], int]:
    """
    Turn request lines into validated SaleItem dictionaries and calculate totals.
    Backend calculates subtotal, discounts, taxes, and total per PRD §39.
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
        unit_price = _money(
            catalog_price if price_given is None else price_given,
            field="unit price",
        )
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
            line["tax_rate"] = min(
                (line["tax"] / base * 100).quantize(RATE_STEP),
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


@transaction.atomic
def create_sale(data: dict[str, Any], *, actor: Any = None) -> Sale:
    """
    Create a sale and execute its full lifecycle atomically (PRD §21, §42).

    Coordinates:
    - Entitlement / active subscription verification
    - Pessimistic locking on Customer row to prevent race conditions
    - Location organization verification
    - Backend pricing and apportionment enforcement
    - Payment parsing (tendered cash / card)
    - Inventory deduction via FIFO
    - Synchronous invoice creation
    - Upfront payment recording
    - Centralized audit event logging
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

    # Lock customer row first to prevent balance/credit race conditions
    customer = load_active_customer(organization, data["customer"].pk, lock=True)
    data["customer"] = customer

    location = data.get("location")
    warehouse = data.get("warehouse")

    if location is not None:
        if location.warehouse.organization_id != organization.pk:
            msg = "The location belongs to another organization."
            raise SaleValidationError(msg, detail={"location_id": str(location.pk)})
        warehouse = location.warehouse
    elif warehouse is not None:
        if warehouse.organization_id != organization.pk:
            msg = "The warehouse belongs to another organization."
            raise SaleValidationError(msg, detail={"warehouse_id": str(warehouse.pk)})
    else:
        from core.applications.warehouse.models import Warehouse
        active_warehouse = Warehouse.objects.for_organization(organization).filter(is_active=True).first()
        if active_warehouse:
            warehouse = active_warehouse
            location = active_warehouse.locations.filter(is_active=True).first()

    has_tracked_items = any(
        getattr(raw.get("product"), "track_inventory", False) for raw in items_data
    )
    if has_tracked_items and warehouse is None:
        msg = "A warehouse is required to deduct inventory for tracked products."
        raise SaleValidationError(msg, detail={"warehouse": "No warehouse found for this organization."})

    data["warehouse"] = warehouse
    data["location"] = location

    lines, totals, price_overrides = _build_lines(
        organization,
        items_data,
        data.pop("discount_amount", 0),
        data.pop("tax_amount", 0),
    )
    payment_result = parse_payment(payment_data, totals["total"])
    paid_now = payment_result.amount
    method = payment_result.method
    payment_reference = payment_result.reference
    change_due = payment_result.change_due

    data.pop("subtotal", None)
    data.pop("total", None)
    data["payment_status"] = payment_status(totals["total"], paid_now)
    data["currency"] = data.get("currency") or getattr(organization, "currency", None) or "USD"
    if not data.get("sale_id"):
        data["sale_id"] = f"SALE-{uuid.uuid4().hex[:8].upper()}"

    details = customer_snapshot(
        customer,
        billing_address=data.get("billing_address"),
        shipping_address=data.get("shipping_address"),
    )
    data["billing_address"] = details["billing_address"] or None
    data["shipping_address"] = details["shipping_address"] or None

    sale = Sale.objects.create(**data, **totals)
    sale.change_due = change_due
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
        actor=actor,
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


@transaction.atomic
def cancel_sale(
    sale: Sale,
    *,
    actor: Any = None,
    reason: str = "",
) -> Sale:
    """
    Cancel an existing sale per PRD §44 (Cancellations & Immutability).

    Reverses stock movements and records compensatory inventory ledger entries.
    Sets cancelled_at, cancelled_by, cancel_reason, and fulfillment_status = CANCELED.
    """
    ensure_subscription_active(sale.organization)

    if sale.cancelled_at is not None:
        raise SaleCancellationError(
            "This sale has already been cancelled.",
            detail={"sale_id": str(sale.pk), "cancelled_at": str(sale.cancelled_at)},
        )

    if sale.returns.exists():
        raise SaleCancellationError(
            "Cannot cancel a sale that has existing returns. Return remaining items instead.",
            detail={"sale_id": str(sale.pk)},
        )

    reverse_sale_inventory_and_ledger(sale, actor=actor, reason=reason)

    sale.cancelled_at = timezone.now()
    sale.cancelled_by = actor or sale.sales_rep
    sale.cancel_reason = reason
    sale.fulfillment_status = SaleFulfillmentStatusChoices.CANCELED
    sale.save(update_fields=["cancelled_at", "cancelled_by", "cancel_reason", "fulfillment_status"])

    record_sale_cancellation_audit_event(sale, actor=actor, reason=reason)
    logger.info(
        "sale.cancelled",
        extra={
            "organization_id": str(sale.organization_id),
            "sale_id": str(sale.pk),
            "reason": reason,
        },
    )
    return sale
