from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from datetime import date


@dataclass(frozen=True, slots=True)
class PurchaseLineSnapshot:
    """Read-only snapshot of a PurchaseItem line for PurchaseInvoice creation."""

    purchase_item_id: Any
    product_id: Any
    variant_id: Any
    description: str
    unit_name: str
    unit_multiplier: Decimal
    quantity_ordered: int
    quantity_received: int
    purchase_cost: Decimal
    discount: Decimal


@dataclass(frozen=True, slots=True)
class PurchaseSnapshot:
    """Read-only snapshot of a Purchase order built without direct model coupling."""

    purchase_id: Any
    purchase_number: str
    organization_id: Any
    vendor_id: Any
    warehouse_id: Any
    ordered_by_id: Any
    vendor_invoice_number: str
    order_date: date | None
    delivery_date: date | None
    other_costs: Decimal
    discount: Decimal
    amount_paid: Decimal
    status: str
    notes: str | None
    is_cancelled: bool
    lines: tuple[PurchaseLineSnapshot, ...]


def build_purchase_snapshot(purchase: Any) -> PurchaseSnapshot:
    """
    Build a PurchaseSnapshot from a Purchase instance (mirroring `build_sale_snapshot`).
    """
    items = list(purchase.items.select_related("product", "variant", "unit").order_by("created_at", "pk"))
    line_snapshots: list[PurchaseLineSnapshot] = []
    for item in items:
        unit_name = (
            item.unit.unit_name
            if item.unit is not None
            else getattr(item.product, "unit_of_measurement", "pcs")
        )
        unit_multiplier = (
            Decimal(str(item.unit.multiplier))
            if item.unit is not None
            else Decimal("1.000")
        )
        line_snapshots.append(
            PurchaseLineSnapshot(
                purchase_item_id=item.pk,
                product_id=item.product_id,
                variant_id=item.variant_id,
                description=getattr(item.product, "name", "") or "",
                unit_name=unit_name or "pcs",
                unit_multiplier=unit_multiplier,
                quantity_ordered=item.quantity_ordered,
                quantity_received=item.quantity_received,
                purchase_cost=Decimal(str(item.purchase_cost)),
                discount=Decimal(str(item.discount or 0)),
            )
        )

    return PurchaseSnapshot(
        purchase_id=purchase.pk,
        purchase_number=purchase.purchase_number,
        organization_id=purchase.organization_id,
        vendor_id=purchase.vendor_id,
        warehouse_id=purchase.warehouse_id,
        ordered_by_id=purchase.ordered_by_id,
        vendor_invoice_number=purchase.vendor_invoice_number or "",
        order_date=purchase.order_date,
        delivery_date=purchase.delivery_date,
        other_costs=Decimal(str(purchase.other_costs or 0)),
        discount=Decimal(str(purchase.discount or 0)),
        amount_paid=Decimal(str(getattr(purchase, "amount_paid", 0) or 0)),
        status=purchase.status,
        notes=purchase.notes,
        is_cancelled=purchase.status == "cancelled",
        lines=tuple(line_snapshots),
    )

