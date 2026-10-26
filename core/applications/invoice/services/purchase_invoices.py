from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from django.core.exceptions import ValidationError
from django.db import transaction

from core.applications.invoice.models import PurchaseInvoice, PurchaseInvoiceItem
from core.applications.invoice.services.purchase_adapters import (
    PurchaseSnapshot,
    build_purchase_snapshot,
)
from core.applications.notification.audit.action import AuditAction
from core.applications.notification.audit.services import record as audit_record
from core.applications.vendors.services import vendor_snapshot
from core.helper.enums import InvoiceStatusChoices, InvoiceTypeChoices
from core.helper.money import quantize_money

if TYPE_CHECKING:
    from core.applications.notification.audit.context import AuditContext
    from core.applications.users.models import Organization

__all__ = [
    "PurchaseInvoiceInput",
    "create_or_update_purchase_invoice",
    "apply_payment_to_purchase_invoice",
]


@dataclass(frozen=True, slots=True)
class PurchaseInvoiceInput:
    """
    Parameter object carrying the 3-step Supplier Invoice payload (UI Pages 1-3).
    """

    billed_to: str | None = None
    invoice_type: str | None = None
    vendor_invoice_number: str | None = None
    issue_date: date | None = None
    due_date: date | None = None
    tax_rate: Decimal | str | int | None = None
    tax_amount: Decimal | str | int | None = None
    discount_amount: Decimal | str | int | None = None
    other_costs: Decimal | str | int | None = None
    notes: str | None = None
    supporting_document: Any = None
    lines: list[dict[str, Any]] | None = None


def _next_purchase_invoice_number(organization: Organization) -> str:
    year = date.today().year
    prefix = f"PINV-{year}-"
    last = (
        PurchaseInvoice.objects.select_for_update()
        .filter(organization=organization, invoice_number__startswith=prefix)
        .order_by("-invoice_number")
        .first()
    )
    last_seq = 0
    if last and last.invoice_number:
        try:
            last_seq = int(last.invoice_number.split("-")[-1])
        except ValueError:
            last_seq = 0
    return f"{prefix}{str(last_seq + 1).zfill(4)}"


def _resolve_status_for_paid(total: Decimal, amount_paid: Decimal) -> str:
    if total > Decimal("0.00") and amount_paid >= total:
        return InvoiceStatusChoices.PAID
    if amount_paid > Decimal("0.00"):
        return InvoiceStatusChoices.PARTIALLY_PAID
    return InvoiceStatusChoices.CONFIRMED


def _prepare_invoice_items(
    snapshot: PurchaseSnapshot,
    lines: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    line_by_item_id = {str(line.purchase_item_id): line for line in snapshot.lines}
    line_by_product_id = {str(line.product_id): line for line in snapshot.lines}

    if not lines:
        prepared_defaults: list[dict[str, Any]] = []
        for matched in snapshot.lines:
            invoice_qty = matched.quantity_received if matched.quantity_received > 0 else matched.quantity_ordered
            prorated_discount = (
                (matched.discount / Decimal(str(matched.quantity_ordered))) * Decimal(str(invoice_qty))
                if matched.quantity_ordered > 0
                else Decimal("0.00")
            )
            prorated_discount = quantize_money(prorated_discount, field="discount_amount")
            line_gross = quantize_money(Decimal(str(invoice_qty)) * matched.purchase_cost, field="line_total")
            line_total_val = max(Decimal("0.00"), line_gross - prorated_discount)
            prepared_defaults.append(
                {
                    "purchase_item_id": matched.purchase_item_id,
                    "product_id": matched.product_id,
                    "variant_id": matched.variant_id,
                    "description": matched.description,
                    "unit_name": matched.unit_name,
                    "unit_multiplier": matched.unit_multiplier,
                    "quantity_ordered": matched.quantity_ordered,
                    "invoice_quantity": invoice_qty,
                    "unit_cost": quantize_money(matched.purchase_cost, field="unit_cost"),
                    "discount_amount": prorated_discount,
                    "line_total": line_total_val,
                }
            )
        return prepared_defaults

    prepared_custom: list[dict[str, Any]] = []
    for raw_line in lines:
        purchase_item_obj = raw_line.get("purchase_item")
        purchase_item_id = str(
            getattr(purchase_item_obj, "pk", purchase_item_obj) or raw_line.get("purchase_item_id") or ""
        )
        product_obj = raw_line.get("product")
        product_id = str(getattr(product_obj, "pk", product_obj) or raw_line.get("product_id") or "")

        matched = line_by_item_id.get(purchase_item_id) or line_by_product_id.get(product_id)
        if matched is None:
            raise ValidationError(
                {"lines": f"Line item does not match any product on purchase order {snapshot.purchase_number}."}
            )

        invoice_qty = int(raw_line.get("invoice_quantity", matched.quantity_received or matched.quantity_ordered))
        if invoice_qty < 1:
            raise ValidationError({"invoice_quantity": "Invoice quantity must be at least 1."})

        raw_unit_cost = raw_line.get("unit_cost", matched.purchase_cost)
        unit_cost_val = quantize_money(raw_unit_cost, field="unit_cost")
        if unit_cost_val < Decimal("0.00"):
            raise ValidationError({"unit_cost": "Unit cost cannot be negative."})

        raw_line_discount = raw_line.get("discount_amount", matched.discount)
        line_discount_val = quantize_money(raw_line_discount or 0, field="discount_amount")
        line_gross = quantize_money(Decimal(str(invoice_qty)) * unit_cost_val, field="line_total")
        if line_discount_val < Decimal("0.00") or line_discount_val > line_gross:
            raise ValidationError({"discount_amount": "Line discount must be between 0 and line gross."})

        line_total_val = max(Decimal("0.00"), line_gross - line_discount_val)
        prepared_custom.append(
            {
                "purchase_item_id": matched.purchase_item_id,
                "product_id": matched.product_id,
                "variant_id": matched.variant_id,
                "description": matched.description,
                "unit_name": matched.unit_name,
                "unit_multiplier": matched.unit_multiplier,
                "quantity_ordered": matched.quantity_ordered,
                "invoice_quantity": invoice_qty,
                "unit_cost": unit_cost_val,
                "discount_amount": line_discount_val,
                "line_total": line_total_val,
            }
        )
    return prepared_custom


def _compute_invoice_totals(
    snapshot: PurchaseSnapshot,
    prepared_items: list[dict[str, Any]],
    payload: PurchaseInvoiceInput,
) -> dict[str, Decimal]:
    subtotal = quantize_money(
        sum((item["line_total"] for item in prepared_items), Decimal("0.00")),
        field="subtotal",
    )
    resolved_discount = quantize_money(
        snapshot.discount if payload.discount_amount is None else payload.discount_amount,
        field="discount_amount",
    )
    resolved_other_costs = quantize_money(
        snapshot.other_costs if payload.other_costs is None else payload.other_costs,
        field="other_costs",
    )
    resolved_tax_rate = Decimal(str(payload.tax_rate or "0.00")).quantize(Decimal("0.01"))
    if payload.tax_amount is not None:
        resolved_tax = quantize_money(payload.tax_amount, field="tax_amount")
    elif resolved_tax_rate > Decimal("0.00"):
        taxable_base = max(Decimal("0.00"), subtotal - resolved_discount)
        resolved_tax = quantize_money((taxable_base * resolved_tax_rate) / Decimal("100.00"), field="tax_amount")
    else:
        resolved_tax = Decimal("0.00")

    total = max(
        Decimal("0.00"),
        quantize_money(subtotal - resolved_discount + resolved_tax + resolved_other_costs, field="total"),
    )
    return {
        "subtotal": subtotal,
        "discount_amount": resolved_discount,
        "other_costs": resolved_other_costs,
        "tax_rate": resolved_tax_rate,
        "tax_amount": resolved_tax,
        "total": total,
    }


def _persist_purchase_invoice(
    *,
    purchase: Any,
    actor: Any,
    snapshot: PurchaseSnapshot,
    payload: PurchaseInvoiceInput,
    totals: dict[str, Decimal],
) -> PurchaseInvoice:
    organization = purchase.organization
    vendor_details = vendor_snapshot(purchase.vendor)
    resolved_billed_to = (
        payload.billed_to.strip()
        if payload.billed_to is not None and payload.billed_to.strip()
        else getattr(organization, "name", "")
    )
    resolved_vendor_inv_no = (
        payload.vendor_invoice_number.strip()
        if payload.vendor_invoice_number is not None
        else snapshot.vendor_invoice_number
    )
    resolved_issue_date = payload.issue_date or snapshot.order_date or date.today()
    resolved_due_date = payload.due_date or snapshot.delivery_date
    resolved_currency = getattr(organization, "currency", None) or "NGN"

    existing = (
        PurchaseInvoice.objects.select_for_update()
        .for_organization(organization)
        .filter(purchase=purchase)
        .not_voided()
        .first()
    )

    if existing is not None:
        existing.billed_to = resolved_billed_to
        existing.invoice_type = payload.invoice_type or existing.invoice_type or InvoiceTypeChoices.SUPPLIER_INVOICE
        existing.vendor_invoice_number = resolved_vendor_inv_no
        existing.issue_date = resolved_issue_date
        existing.due_date = resolved_due_date
        existing.subtotal = totals["subtotal"]
        existing.discount_amount = totals["discount_amount"]
        existing.tax_amount = totals["tax_amount"]
        existing.tax_rate = totals["tax_rate"]
        existing.other_costs = totals["other_costs"]
        existing.total = totals["total"]
        existing.amount_paid = snapshot.amount_paid
        existing.status = _resolve_status_for_paid(totals["total"], snapshot.amount_paid)
        if payload.notes is not None:
            existing.notes = payload.notes
        if payload.supporting_document is not None:
            existing.supporting_document = payload.supporting_document
        for key, val in vendor_details.items():
            setattr(existing, key, val)
        existing.clean()
        existing.save()
        existing.items.all().delete()
        return existing

    invoice_number = _next_purchase_invoice_number(organization)
    purchase_invoice = PurchaseInvoice(
        organization=organization,
        vendor=purchase.vendor,
        purchase=purchase,
        warehouse=purchase.warehouse,
        recorded_by=actor or purchase.ordered_by,
        billed_to=resolved_billed_to,
        invoice_type=payload.invoice_type or InvoiceTypeChoices.SUPPLIER_INVOICE,
        invoice_number=invoice_number,
        vendor_invoice_number=resolved_vendor_inv_no,
        issue_date=resolved_issue_date,
        due_date=resolved_due_date,
        status=_resolve_status_for_paid(totals["total"], snapshot.amount_paid),
        subtotal=totals["subtotal"],
        discount_amount=totals["discount_amount"],
        tax_amount=totals["tax_amount"],
        tax_rate=totals["tax_rate"],
        other_costs=totals["other_costs"],
        total=totals["total"],
        amount_paid=snapshot.amount_paid,
        currency=resolved_currency,
        supporting_document=payload.supporting_document or purchase.supporting_document,
        notes=payload.notes if payload.notes is not None else purchase.notes,
        **vendor_details,
    )
    purchase_invoice.clean()
    purchase_invoice.save()
    return purchase_invoice


@transaction.atomic
def create_or_update_purchase_invoice(
    purchase: Any,
    *,
    actor: Any = None,
    payload: PurchaseInvoiceInput | None = None,
    audit_context: AuditContext | None = None,
) -> PurchaseInvoice:
    """
    Creates or updates the PurchaseInvoice for a Purchase Order (PRD §18, UI Pages 1-3 & 7).
    Organization is derived directly from `purchase.organization`.
    """
    organization = purchase.organization
    resolved_payload = payload or PurchaseInvoiceInput()
    snapshot = build_purchase_snapshot(purchase)
    if str(snapshot.organization_id) != str(organization.pk):
        raise ValidationError({"purchase": "Purchase order does not belong to this organization."})

    if snapshot.is_cancelled:
        raise ValidationError({"purchase": "Cannot record a supplier invoice for a cancelled purchase order."})

    if not snapshot.lines:
        raise ValidationError({"items": "Cannot record a supplier invoice for a purchase order with no line items."})

    prepared_items = _prepare_invoice_items(snapshot, resolved_payload.lines)
    totals = _compute_invoice_totals(snapshot, prepared_items, resolved_payload)
    purchase_invoice = _persist_purchase_invoice(
        purchase=purchase,
        actor=actor,
        snapshot=snapshot,
        payload=resolved_payload,
        totals=totals,
    )
    PurchaseInvoiceItem.objects.bulk_create(
        [PurchaseInvoiceItem(purchase_invoice=purchase_invoice, **line_data) for line_data in prepared_items]
    )

    audit_record(
        action=AuditAction.PURCHASE_INVOICE_RECORDED,
        organization=organization,
        actor=actor,
        resource=purchase_invoice,
        context=audit_context,
        metadata={
            "purchase_id": str(purchase.pk),
            "purchase_number": snapshot.purchase_number,
            "invoice_number": purchase_invoice.invoice_number,
            "vendor_invoice_number": purchase_invoice.vendor_invoice_number,
            "total": str(purchase_invoice.total),
        },
    )
    return purchase_invoice


@transaction.atomic
def apply_payment_to_purchase_invoice(
    purchase: Any,
    *,
    new_amount_paid: Decimal,
) -> PurchaseInvoice | None:
    """
    Synchronizes `amount_paid` and `status` on the active PurchaseInvoice when a supplier payment is recorded.
    """
    invoice = (
        PurchaseInvoice.objects.select_for_update()
        .filter(purchase=purchase)
        .exclude(status=InvoiceStatusChoices.CANCELLED)
        .first()
    )
    if invoice is None:
        return None

    paid_val = quantize_money(new_amount_paid, field="amount_paid")
    invoice.amount_paid = paid_val
    invoice.status = _resolve_status_for_paid(invoice.total, paid_val)
    invoice.save(update_fields=["amount_paid", "status", "updated_at"])
    return invoice

