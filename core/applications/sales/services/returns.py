from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from django.db import transaction

from core.applications.inventory.services import process_return_inventory_and_ledger
from core.applications.payment.services import record_refund_for_return
from core.applications.sales.models import Sale, SaleItem, SaleReturn, SaleReturnItem
from core.applications.sales.services.audit import record_sale_return_audit_event
from core.applications.sales.services.exceptions import SaleValidationError
from core.applications.subscriptions.services.entitlements import (
    ensure_subscription_active,
)
from core.helper.enums import SaleFulfillmentStatusChoices
from core.helper.money import CENT, quantize_money

logger = logging.getLogger(__name__)


def _money(value: Any, *, field: str = "amount") -> Decimal:
    """Safely parse and quantize a money value to whole cents."""
    try:
        return quantize_money(value, field=field)
    except ValueError as exc:
        raise SaleValidationError(f"Invalid {field}.", detail={field: str(value)}) from exc


def _returnable_amount(item: SaleItem, quantity: int) -> Decimal:
    """The most that can be refunded for `quantity` of this line."""
    return (item.total * quantity / item.quantity).quantize(CENT)


@transaction.atomic
def create_sale_return(data: dict[str, Any], *, actor: Any = None) -> SaleReturn:
    """
    Create a SaleReturn and its items, apply inventory restocking, and execute
    the refund (PRD §23).

    Atomic protections:
    - Enforces active subscription
    - Selects sale items with row-level pessimistic locking (`select_for_update`)
    - Checks returnable quantity bounds against `item.quantity - item.returned_quantity`
    - Validates refund totals against computed item return values
    - Persists `returned_quantity` to `SaleItem` rows
    - Updates original sale fulfillment status (RETURNED if fully returned)
    - Restocks items via inventory companion
    - Records refund against the sale's invoice payment
    - Logs centralized audit event
    """
    data = dict(data)
    organization = data.get("organization")
    if organization is not None:
        ensure_subscription_active(organization)

    original_sale: Sale = data["original_sale"]
    if organization is not None and original_sale.organization_id != organization.pk:
        msg = "The sale belongs to another organization."
        raise SaleValidationError(msg, detail={"sale_id": str(original_sale.pk)})

    if original_sale.cancelled_at is not None:
        msg = "Cannot return items for a cancelled sale."
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
        available_returnable = sale_item.quantity - sale_item.returned_quantity
        if requested[sale_item.pk] > available_returnable:
            msg = "More is being returned than the sale still has returnable."
            raise SaleValidationError(
                msg,
                detail={
                    "sale_item_id": str(sale_item.pk),
                    "returnable": available_returnable,
                },
            )

        max_allowed = _returnable_amount(sale_item, quantity)
        if item_data.get("refund_amount") is None:
            refund = max_allowed
        else:
            refund = _money(item_data["refund_amount"], field="refund amount")
            if not Decimal(0) <= refund <= max_allowed:
                msg = "A refund exceeds the value of the returned quantity."
                raise SaleValidationError(
                    msg,
                    detail={"sale_item_id": str(sale_item.pk), "max_allowed": str(max_allowed)},
                )
        item_data["refund_amount"] = refund
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
        # Atomically increment returned_quantity on the SaleItem
        si = sale_items[item_data["sale_item"].pk]
        si.returned_quantity += item_data["quantity"]
        si.save(update_fields=["returned_quantity"])

    # Update original sale status if all items returned
    all_returned = all(item.returned_quantity >= item.quantity for item in sale_items.values())
    if all_returned:
        original_sale.fulfillment_status = SaleFulfillmentStatusChoices.RETURNED
        original_sale.save(update_fields=["fulfillment_status"])

    process_return_inventory_and_ledger(sale_return)
    record_refund_for_return(sale_return)
    record_sale_return_audit_event(sale_return, actor=actor)

    logger.info(
        "sale_return.created",
        extra={
            "organization_id": str(sale_return.organization_id),
            "sale_return_id": str(sale_return.pk),
            "original_sale_id": str(original_sale.pk),
            "refund_amount": str(refund_total),
        },
    )
    return sale_return
