from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import connection
from django.db import transaction
from django.db.models import Case
from django.db.models import FloatField
from django.db.models.functions import Cast
from django.db.models import Count
from django.db.models import DecimalField
from django.db.models import Exists
from django.db.models import ExpressionWrapper
from django.db.models import F
from django.db.models import OuterRef
from django.db.models import Q
from django.db.models import Subquery
from django.db.models import Sum
from django.db.models import Value
from django.db.models import When
from django.db.models.functions import Coalesce
from django.db.models.functions import Greatest
from django.utils import timezone

from core.applications.inventory.services import (
    InsufficientStock,
    process_purchase_return_inventory_and_ledger,
    record_purchase_receipt_inventory_and_ledger,
)
from core.applications.invoice.services.purchase_invoices import (
    PurchaseInvoiceInput,
    create_or_update_purchase_invoice,
)
from core.applications.notification.audit.action import AuditAction
from core.applications.notification.audit.services import record as audit_record
from core.applications.payment.exceptions import PaymentValidationError
from core.applications.payment.services import (
    PurchasePaymentInput,
    record_purchase_payment,
)
from core.applications.products.services import resolve_or_create_product_batch
from core.applications.purchase.exceptions import (
    CannotDeleteReceivedPurchaseError,
    PurchaseCancellationError,
    PurchaseReceiveError,
    PurchaseReturnError,
    PurchaseValidationError,
)
from core.applications.purchase.models import (
    Purchase,
    PurchaseItem,
    PurchaseReturn,
    PurchaseReturnItem,
)
from core.applications.subscriptions.services.entitlements import (
    ensure_subscription_active,
)
from core.applications.vendors.services import load_vendor_for_organization
from core.helper.enums import (
    InvoiceStatusChoices,
    PurchasePaymentStatusChoices,
    PurchaseStatusChoices,
)

from core.applications.invoice.models import PurchaseInvoice

if TYPE_CHECKING:
    from core.applications.payment.models import PurchasePayment
    from core.applications.users.models import Organization, User

__all__ = [
    "PurchaseInvoiceInput",
    "PurchasePaymentInput",
    "create_purchase_order",
    "update_purchase_order",
    "delete_purchase_order",
    "cancel_purchase_order",
    "receive_purchase_order",
    "record_purchase_invoice",
    "record_supplier_payment",
    "create_purchase_return",
    "get_purchase_summary",
    "get_purchase_available_actions",
]


def _validate_purchase_items_for_organization(
    organization: Organization,
    items_data: list[dict[str, Any]],
) -> None:
    """Validates that every line item's product, variant, and unit belong to the organization."""
    for idx, item_data in enumerate(items_data):
        product = item_data.get("product")
        if product and getattr(product, "organization_id", None) != organization.id:
            raise PurchaseValidationError(
                f"Product '{product.name}' does not belong to this organization.",
                detail={"line": idx, "product": str(product.pk)},
            )
        variant = item_data.get("variant")
        if variant and product and getattr(variant, "product_id", None) != product.id:
            raise PurchaseValidationError(
                f"Variant '{variant}' does not belong to product '{product.name}'.",
                detail={"line": idx, "variant": str(variant.pk)},
            )
        unit = item_data.get("unit")
        if unit and product and getattr(unit, "product_id", None) != product.id:
            raise PurchaseValidationError(
                f"Unit conversion '{unit}' does not belong to product '{product.name}'.",
                detail={"line": idx, "unit": str(unit.pk)},
            )
        quantity_ordered = int(item_data.get("quantity_ordered") or 0)
        if quantity_ordered <= 0:
            raise PurchaseValidationError(
                "Ordered quantity must be greater than zero.",
                detail={"line": idx, "quantity_ordered": quantity_ordered},
            )
        if item_data.get("purchase_cost") is None and product is not None:
            variant_cost = getattr(variant, "purchase_cost", None) if variant else None
            base_cost = Decimal(
                str(
                    variant_cost
                    if variant_cost is not None
                    else (getattr(product, "purchase_cost", None) or Decimal("0.00"))
                )
            )
            multiplier = Decimal(str(unit.multiplier)) if unit else Decimal("1.000")
            item_data["purchase_cost"] = (base_cost * multiplier).quantize(Decimal("0.01"))
        if item_data.get("discount") is None:
            item_data["discount"] = Decimal("0.00")


@transaction.atomic
def create_purchase_order(
    *,
    ordered_by: User,
    vendor: Any,
    warehouse: Any,
    items_data: list[dict[str, Any]] | None = None,
    audit_context: Any = None,
    **purchase_fields: Any,
) -> Purchase:
    """
    Creates a new Purchase Order and its line items (PRD §10, §18, UI Pages 16-18).
    Organization is derived from `warehouse.organization` (or explicit `organization` in `purchase_fields` if provided).
    """
    organization = purchase_fields.pop("organization", None) or getattr(warehouse, "organization", None)
    ensure_subscription_active(organization)

    try:
        load_vendor_for_organization(organization, vendor.pk if hasattr(vendor, "pk") else vendor)
    except (ValueError, DjangoValidationError) as exc:
        raise PurchaseValidationError(str(exc), detail={"vendor": str(vendor)}) from exc

    if getattr(warehouse, "organization_id", None) != organization.id:
        raise PurchaseValidationError(
            "Warehouse does not belong to this organization.",
            detail={"warehouse": str(getattr(warehouse, "pk", warehouse))},
        )

    resolved_items = items_data or []
    _validate_purchase_items_for_organization(organization, resolved_items)

    purchase = Purchase(
        organization=organization,
        ordered_by=ordered_by,
        vendor=vendor,
        warehouse=warehouse,
        **purchase_fields,
    )
    purchase.clean()
    purchase.save()

    for item_data in resolved_items:
        item = PurchaseItem(purchase=purchase, **item_data)
        item.clean()
        item.save()

    audit_record(
        action=AuditAction.PURCHASE_ORDER_CREATED,
        organization=purchase.organization,
        actor=ordered_by,
        resource=purchase,
        context=audit_context,
        metadata={
            "purchase_number": purchase.purchase_number,
            "items_count": len(resolved_items),
        },
    )
    return purchase


@transaction.atomic
def update_purchase_order(
    *,
    purchase: Purchase,
    actor: User,
    items_data: list[dict[str, Any]] | None = None,
    audit_context: Any = None,
    **update_fields: Any,
) -> Purchase:
    """
    Updates a Purchase Order and optionally replaces its line items if no goods have been received yet (PRD §18).
    """
    ensure_subscription_active(purchase.organization)

    purchase = Purchase.objects.select_for_update().get(pk=purchase.pk)
    if purchase.status == PurchaseStatusChoices.CANCELLED:
        raise PurchaseValidationError(
            "Cannot modify a cancelled purchase order.",
            detail={"purchase_id": str(purchase.pk), "status": str(purchase.status)},
        )

    old_status = purchase.status
    has_received = purchase.items.filter(quantity_received__gt=0).exists()

    if "vendor" in update_fields and update_fields["vendor"] != purchase.vendor:
        if has_received:
            raise PurchaseValidationError(
                "Cannot change vendor on a purchase order that has already received goods."
            )
        vendor = update_fields["vendor"]
        try:
            load_vendor_for_organization(purchase.organization, vendor.pk if hasattr(vendor, "pk") else vendor)
        except (ValueError, DjangoValidationError) as exc:
            raise PurchaseValidationError(str(exc), detail={"vendor": str(vendor)}) from exc

    if "warehouse" in update_fields and update_fields["warehouse"] != purchase.warehouse:
        if has_received:
            raise PurchaseValidationError(
                "Cannot change warehouse on a purchase order that has already received goods."
            )
        warehouse = update_fields["warehouse"]
        if getattr(warehouse, "organization_id", None) != purchase.organization_id:
            raise PurchaseValidationError("Warehouse does not belong to this organization.")

    if items_data is not None:
        if has_received:
            raise PurchaseValidationError(
                "Cannot modify line items on a purchase order that has already received goods."
            )
        _validate_purchase_items_for_organization(purchase.organization, items_data)

    for attr, val in update_fields.items():
        setattr(purchase, attr, val)
    purchase.clean()
    purchase.save()

    if items_data is not None:
        purchase.items.all().delete()
        for item_data in items_data:
            item = PurchaseItem(purchase=purchase, **item_data)
            item.clean()
            item.save()

    audit_action = (
        AuditAction.PURCHASE_ORDER_APPROVED
        if old_status != purchase.status and purchase.status == PurchaseStatusChoices.ORDERED
        else AuditAction.PURCHASE_ORDER_UPDATED
    )
    audit_record(
        action=audit_action,
        organization=purchase.organization,
        actor=actor,
        resource=purchase,
        previous_values={"status": old_status},
        new_values={"status": purchase.status},
        context=audit_context,
    )
    return purchase


@transaction.atomic
def delete_purchase_order(
    *,
    purchase: Purchase,
    actor: User,
    audit_context: Any = None,
) -> None:
    """
    Hard-deletes a Purchase Order only if no goods have been received yet (PRD §18).
    """
    ensure_subscription_active(purchase.organization)
    purchase = Purchase.objects.select_for_update().get(pk=purchase.pk)

    if purchase.items.filter(quantity_received__gt=0).exists():
        raise CannotDeleteReceivedPurchaseError()

    if hasattr(purchase, "payments") and purchase.payments.filter(is_voided=False).exists():
        raise CannotDeleteReceivedPurchaseError(
            "Cannot delete a purchase order that has recorded supplier payments."
        )

    audit_record(
        action=AuditAction.PURCHASE_ORDER_DELETED,
        organization=purchase.organization,
        actor=actor,
        resource=purchase,
        context=audit_context,
        reason="Purchase order deleted while still in unreceived state.",
    )
    purchase.delete()


@transaction.atomic
def cancel_purchase_order(
    *,
    purchase: Purchase,
    actor: User,
    reason: str = "",
    audit_context: Any = None,
) -> Purchase:
    """
    Cancels a DRAFT or ORDERED Purchase Order before any goods have been received (UI Page 14).
    """
    ensure_subscription_active(purchase.organization)
    purchase = Purchase.objects.select_for_update().get(pk=purchase.pk)

    if purchase.status == PurchaseStatusChoices.CANCELLED:
        raise PurchaseCancellationError(
            "This purchase order is already cancelled.",
            detail={"purchase_id": str(purchase.pk)},
        )

    if purchase.status not in (PurchaseStatusChoices.DRAFT, PurchaseStatusChoices.ORDERED):
        raise PurchaseCancellationError(
            "Only draft or ordered purchase orders can be cancelled before goods are received.",
            detail={"purchase_id": str(purchase.pk), "status": str(purchase.status)},
        )

    if purchase.items.filter(quantity_received__gt=0).exists():
        raise PurchaseCancellationError(
            "Cannot cancel a purchase order that has already received goods. Use a supplier return instead.",
            detail={"purchase_id": str(purchase.pk)},
        )

    if hasattr(purchase, "payments") and purchase.payments.filter(is_voided=False).exists():
        raise PurchaseCancellationError(
            "Cannot cancel a purchase order with active supplier payments.",
            detail={"purchase_id": str(purchase.pk)},
        )

    old_status = purchase.status
    purchase.status = PurchaseStatusChoices.CANCELLED
    purchase.cancelled_at = timezone.now()
    purchase.cancelled_by = actor
    purchase.cancel_reason = reason or "Cancelled by user."
    purchase.save(
        update_fields=[
            "status",
            "cancelled_at",
            "cancelled_by",
            "cancel_reason",
            "updated_at",
        ]
    )

    if hasattr(purchase, "purchase_invoices"):
        for inv in purchase.purchase_invoices.exclude(status=InvoiceStatusChoices.CANCELLED):
            inv.status = InvoiceStatusChoices.CANCELLED
            inv.voided_by = actor
            inv.voided_at = purchase.cancelled_at
            inv.void_reason = purchase.cancel_reason
            inv.save(update_fields=["status", "voided_by", "voided_at", "void_reason", "updated_at"])

    audit_record(
        action=AuditAction.PURCHASE_ORDER_CANCELLED,
        organization=purchase.organization,
        actor=actor,
        resource=purchase,
        previous_values={"status": old_status},
        new_values={"status": purchase.status},
        reason=purchase.cancel_reason,
        context=audit_context,
    )
    return purchase


@transaction.atomic
def receive_purchase_order(
    purchase: Purchase,
    items_received: list[dict[str, Any]],
    user: User,
    *,
    audit_context: Any = None,
) -> Purchase:
    """
    Receives physical goods against a Purchase Order (PRD §10, §18, UI Page 19):
    - Locks `purchase` and `PurchaseItem` rows in deterministic order.
    - Resolves or creates `ProductBatch` via `products.services.resolve_or_create_product_batch`
      when `batch`, `batch_number`, or `expiry_date` is supplied.
    - Converts received quantities to base units using `item.unit.multiplier` (default `1.000`).
    - Delegates inventory stock increment, `InventoryLedgerEntry`, and double-entry `FinancialLedgerEntry`
      (`Debit: INVENTORY`, `Credit: ACCOUNTS_PAYABLE`) to `inventory.services.record_purchase_receipt_inventory_and_ledger`.
    - Transitions `purchase.status` to `PARTIALLY_RECEIVED` or `RECEIVED`.
    """
    ensure_subscription_active(purchase.organization)

    purchase = Purchase.objects.select_for_update().get(pk=purchase.pk)
    if purchase.status == PurchaseStatusChoices.CANCELLED:
        raise PurchaseReceiveError(
            "Cannot receive goods against a cancelled purchase order.",
            detail={"purchase_id": str(purchase.pk)},
        )

    if not items_received:
        raise PurchaseReceiveError("At least one item must be provided to receive goods.")

    locked_items = {
        str(it.pk).replace("-", ""): it
        for it in purchase.items.select_for_update(of=('self',))
        .select_related("product", "variant", "unit")
        .order_by("product_id", "pk")
    }

    movement_lines: list[dict[str, Any]] = []
    received_cost_total = Decimal("0.00")

    for received_item_data in items_received:
        raw_item_id = str(received_item_data.get("item_id"))
        normalized_item_id = raw_item_id.replace("-", "")
        item = locked_items.get(normalized_item_id)
        if item is None:
            raise PurchaseReceiveError(
                f"Purchase item '{raw_item_id}' does not belong to purchase order {purchase.purchase_number}.",
                detail={"item_id": raw_item_id},
            )

        received_quantity = int(received_item_data.get("quantity_received") or 0)
        if received_quantity <= 0:
            raise PurchaseReceiveError(
                f"Received quantity must be greater than zero for '{item.product.name}'.",
                detail={"item_id": raw_item_id, "quantity_received": received_quantity},
            )

        if item.quantity_received + received_quantity > item.quantity_ordered:
            raise PurchaseReceiveError(
                f"Cannot receive {received_quantity} units of '{item.product.name}'. "
                f"Already received {item.quantity_received} of {item.quantity_ordered} ordered.",
                detail={
                    "item_id": raw_item_id,
                    "quantity_received": received_quantity,
                    "already_received": item.quantity_received,
                    "quantity_ordered": item.quantity_ordered,
                },
            )

        previous_received_value = Decimal(str(item.received_value))
        item.quantity_received += received_quantity
        item.save(update_fields=["quantity_received", "updated_at"])
        new_received_value = Decimal(str(item.received_value))
        incremental_line_cost = max(Decimal("0.00"), new_received_value - previous_received_value)
        received_cost_total += incremental_line_cost

        multiplier = Decimal(str(item.unit.multiplier)) if item.unit else Decimal("1.000")
        base_quantity_moved = int((Decimal(str(received_quantity)) * multiplier).to_integral_value())
        base_unit_cost = (Decimal(str(item.purchase_cost)) / multiplier).quantize(Decimal("0.01"))

        batch = resolve_or_create_product_batch(
            product=item.product,
            batch_id=received_item_data.get("batch"),
            batch_number=received_item_data.get("batch_number"),
            expiry_date=received_item_data.get("expiry_date"),
            cost_price=base_unit_cost,
        )

        movement_lines.append(
            {
                "product": item.product,
                "variant": item.variant,
                "batch": batch,
                "base_quantity_moved": base_quantity_moved,
                "base_unit_cost": base_unit_cost,
            }
        )

    record_purchase_receipt_inventory_and_ledger(
        purchase=purchase,
        movement_lines=movement_lines,
        received_cost_total=received_cost_total.quantize(Decimal("0.01")),
        user=user,
    )

    purchase.refresh_from_db()
    all_items = list(purchase.items.all())
    if all_items and all(it.quantity_received >= it.quantity_ordered for it in all_items):
        purchase.status = PurchaseStatusChoices.RECEIVED
    elif any(it.quantity_received > 0 for it in all_items):
        purchase.status = PurchaseStatusChoices.PARTIALLY_RECEIVED

    purchase.received_by = user
    purchase.save(update_fields=["status", "received_by", "updated_at"])

    audit_record(
        action=AuditAction.PURCHASE_ORDER_RECEIVED,
        organization=purchase.organization,
        actor=user,
        resource=purchase,
        context=audit_context,
        metadata={
            "items_received": len(items_received),
            "status": purchase.status,
            "received_cost_total": str(received_cost_total.quantize(Decimal("0.01"))),
        },
    )
    return purchase


@transaction.atomic
def record_purchase_invoice(
    *,
    purchase: Purchase,
    actor: User,
    payload: PurchaseInvoiceInput | None = None,
    audit_context: Any = None,
) -> PurchaseInvoice:
    """
    Records or updates a Supplier Invoice (`PurchaseInvoice`) against a Purchase Order (UI Pages 1-3 & 7).
    """
    ensure_subscription_active(purchase.organization)
    purchase = Purchase.objects.select_for_update().get(pk=purchase.pk)
    resolved_payload = payload or PurchaseInvoiceInput()

    if purchase.status == PurchaseStatusChoices.CANCELLED:
        raise PurchaseValidationError(
            "Cannot record a supplier invoice for a cancelled purchase order.",
            detail={"purchase_id": str(purchase.pk)},
        )

    purchase_invoice = create_or_update_purchase_invoice(
        purchase=purchase,
        actor=actor,
        payload=resolved_payload,
        audit_context=audit_context,
    )

    updated_fields = ["discount", "other_costs", "updated_at"]
    purchase.discount = purchase_invoice.discount_amount
    purchase.other_costs = purchase_invoice.other_costs
    if resolved_payload.vendor_invoice_number:
        purchase.vendor_invoice_number = resolved_payload.vendor_invoice_number
        updated_fields.append("vendor_invoice_number")
    if resolved_payload.supporting_document is not None:
        purchase.supporting_document = resolved_payload.supporting_document
        updated_fields.append("supporting_document")
    purchase.save(update_fields=updated_fields)

    return purchase_invoice


@transaction.atomic
def record_supplier_payment(
    *,
    purchase: Purchase,
    actor: User,
    payload: PurchasePaymentInput,
    audit_context: Any = None,
) -> PurchasePayment:
    """
    Records a supplier payment against a Purchase Order via `payment.services.record_purchase_payment`
    (UI Page 15).
    """
    ensure_subscription_active(purchase.organization)

    try:
        result = record_purchase_payment(
            purchase=purchase,
            actor=actor,
            payload=payload,
        )
    except PaymentValidationError:
        raise

    audit_record(
        action=AuditAction.PURCHASE_PAYMENT_RECORDED,
        organization=purchase.organization,
        actor=actor,
        resource=result.purchase_payment,
        context=audit_context,
        metadata={
            "purchase_number": purchase.purchase_number,
            "receipt_number": result.purchase_payment.receipt_number,
            "amount": str(result.purchase_payment.amount),
            "balance_after": str(result.purchase_payment.balance_after),
        },
    )
    return result.purchase_payment


@transaction.atomic
def create_purchase_return(
    *,
    purchase: Purchase,
    actor: User,
    items: list[dict[str, Any]],
    reason: str = "",
    audit_context: Any = None,
) -> PurchaseReturn:
    """
    Creates a `PurchaseReturn` for returning received goods to the supplier (UI Page 7 Returns tab):
    - Validates that returned quantity does not exceed `quantity_received - already_returned`.
    - Creates `PurchaseReturn` and `PurchaseReturnItem` rows.
    - Delegates inventory stock deduction, negative `InventoryLedgerEntry`, and double-entry
      `FinancialLedgerEntry` (`Debit: ACCOUNTS_PAYABLE`, `Credit: INVENTORY`) to
      `inventory.services.process_purchase_return_inventory_and_ledger`.
    """
    ensure_subscription_active(purchase.organization)
    purchase = Purchase.objects.select_for_update().get(pk=purchase.pk)

    if purchase.status == PurchaseStatusChoices.CANCELLED:
        raise PurchaseReturnError(
            "Cannot record a return against a cancelled purchase order.",
            detail={"purchase_id": str(purchase.pk)},
        )

    if not items:
        raise PurchaseReturnError("At least one item must be provided for a supplier return.")

    locked_items = {
        str(it.pk).replace("-", ""): it
        for it in purchase.items.select_for_update(of=('self',))
        .select_related("product", "variant", "unit")
        .order_by("product_id", "pk")
    }

    purchase_return = PurchaseReturn.objects.create(
        organization=purchase.organization,
        purchase=purchase,
        returned_by=actor,
        reason=reason or "",
        total_refund_amount=Decimal("0.00"),
    )

    total_refund = Decimal("0.00")
    for item_data in items:
        raw_item_id = str(item_data.get("purchase_item"))
        normalized_item_id = raw_item_id.replace("-", "")
        p_item = locked_items.get(normalized_item_id)
        if p_item is None:
            raise PurchaseReturnError(
                f"Purchase item '{raw_item_id}' does not belong to purchase order {purchase.purchase_number}.",
                detail={"purchase_item": raw_item_id},
            )

        qty_to_return = int(item_data.get("quantity") or 0)
        if qty_to_return <= 0:
            raise PurchaseReturnError(
                f"Return quantity must be greater than zero for '{p_item.product.name}'.",
                detail={"purchase_item": raw_item_id, "quantity": qty_to_return},
            )

        already_returned = (
            PurchaseReturnItem.objects.filter(purchase_item=p_item)
            .exclude(purchase_return=purchase_return)
            .aggregate(total=Sum("quantity"))["total"]
            or 0
        )
        max_returnable = p_item.quantity_received - already_returned
        if qty_to_return > max_returnable:
            raise PurchaseReturnError(
                f"Cannot return {qty_to_return} units of '{p_item.product.name}'. "
                f"Maximum returnable is {max_returnable} (received {p_item.quantity_received}, already returned {already_returned}).",
                detail={
                    "purchase_item": raw_item_id,
                    "quantity": qty_to_return,
                    "max_returnable": max_returnable,
                },
            )

        unit_cost = Decimal(str(p_item.purchase_cost)).quantize(Decimal("0.01"))
        line_total = (unit_cost * Decimal(str(qty_to_return))).quantize(Decimal("0.01"))
        total_refund += line_total

        PurchaseReturnItem.objects.create(
            purchase_return=purchase_return,
            purchase_item=p_item,
            batch=item_data.get("batch"),
            quantity=qty_to_return,
            unit_cost=unit_cost,
            line_total=line_total,
        )

    purchase_return.total_refund_amount = total_refund
    purchase_return.save(update_fields=["total_refund_amount", "updated_at"])

    try:
        process_purchase_return_inventory_and_ledger(purchase_return, actor=actor)
    except InsufficientStock as exc:
        raise PurchaseReturnError(
            str(exc),
            detail={
                "product": str(exc.product.pk),
                "requested": exc.requested,
                "available": exc.available,
            },
        ) from exc

    audit_record(
        action=AuditAction.PURCHASE_RETURN_CREATED,
        organization=purchase.organization,
        actor=actor,
        resource=purchase_return,
        context=audit_context,
        metadata={
            "purchase_number": purchase.purchase_number,
            "return_number": purchase_return.return_number,
            "total_refund_amount": str(purchase_return.total_refund_amount),
        },
    )
    return purchase_return


def _fractional(expression):
    """
    Make the division that follows keep its fraction. PostgreSQL divides `numeric`
    exactly. SQLite stores whole-valued decimals (10.00) as integers and would
    integer-divide (10 / 3 = 3), so there the numerator is cast to REAL first; the
    result is quantized to cents afterwards, so the float step can't show.
    """
    if connection.vendor == "sqlite":
        return Cast(expression, output_field=FloatField())
    return expression


def get_purchase_summary(organization: Organization, *, queryset=None) -> dict[str, Any]:
    """
    Computes the KPI summary cards for the Purchase Order List screen (UI Pages 4-6).

    `queryset` lets the caller pass purchases already narrowed to what the caller
    may see (e.g. their locations, PRD §35). It must belong to `organization`.

    Computed entirely in the database: per-purchase values come from correlated
    subqueries over the items and supplier invoices, then a single aggregate
    pass. Memory and round trips stay constant however many purchases exist.
    The arithmetic mirrors the Purchase / PurchaseItem properties exactly
    (ordered_value, total_value, balance_due); test_purchase_summary_matches_model
    keeps the two in step.
    """
    money = DecimalField(max_digits=20, decimal_places=4)
    zero = Value(Decimal("0"), output_field=money)
    s, p = PurchaseStatusChoices, PurchasePaymentStatusChoices

    # Per item, mirroring PurchaseItem.ordered_value / received_value.
    item_ordered = Greatest(
        ExpressionWrapper(F("quantity_ordered") * F("purchase_cost") - F("discount"), output_field=money), zero
    )
    item_received = Greatest(
        ExpressionWrapper(
            F("quantity_received") * F("purchase_cost")
            - Case(
                When(
                    quantity_ordered__gt=0,
                    then=_fractional(F("discount") * F("quantity_received")) / F("quantity_ordered"),
                ),
                default=zero,
                output_field=money,
            ),
            output_field=money,
        ),
        zero,
    )

    def item_sum(expression):
        rows = (
            PurchaseItem.objects.filter(purchase=OuterRef("pk"))
            .order_by()
            .values("purchase")
            .annotate(total=Sum(expression, output_field=money))
            .values("total")[:1]
        )
        return Coalesce(Subquery(rows, output_field=money), zero)

    # The active (newest non-voided) supplier invoice, as Purchase.balance_due picks it.
    active_invoice_total = Subquery(
        PurchaseInvoice.objects.filter(purchase=OuterRef("pk"))
        .exclude(status=InvoiceStatusChoices.CANCELLED)
        .order_by("-created_at")
        .values("total")[:1],
        output_field=money,
    )
    extras = ExpressionWrapper(F("other_costs") - F("discount"), output_field=money)

    base = queryset if queryset is not None else Purchase.objects.all()
    per_purchase = (
        base.for_organization(organization)
        .order_by()
        .annotate(
            _ordered=ExpressionWrapper(item_sum(item_ordered) + extras, output_field=money),
            _received=ExpressionWrapper(item_sum(item_received) + extras, output_field=money),
            _has_received=Exists(PurchaseItem.objects.filter(purchase=OuterRef("pk"), quantity_received__gt=0)),
            _invoice_total=active_invoice_total,
        )
        .annotate(
            _payable=Case(
                When(_invoice_total__isnull=False, then=F("_invoice_total")),
                When(_has_received=True, then=F("_received")),
                default=F("_ordered"),
                output_field=money,
            ),
        )
        .annotate(
            _balance=Greatest(
                ExpressionWrapper(F("_payable") - Coalesce(F("amount_paid"), zero), output_field=money), zero
            ),
        )
    )

    live = ~Q(status=s.CANCELLED)
    totals = per_purchase.aggregate(
        total_purchase_orders=Count("pk"),
        pending_delivery_count=Count("pk", filter=Q(status__in=[s.DRAFT, s.ORDERED])),
        partially_received_count=Count("pk", filter=Q(status=s.PARTIALLY_RECEIVED)),
        received_count=Count("pk", filter=Q(status=s.RECEIVED)),
        cancelled_count=Count("pk", filter=Q(status=s.CANCELLED)),
        unpaid_orders_count=Count("pk", filter=live & Q(payment_status=p.UNPAID)),
        partially_paid_orders_count=Count("pk", filter=live & Q(payment_status=p.PARTIALLY_PAID)),
        paid_orders_count=Count("pk", filter=live & Q(payment_status=p.PAID)),
        total_spend=Sum("_received", filter=live & Q(_has_received=True), output_field=money),
        total_ordered_value=Sum("_ordered", filter=live, output_field=money),
        total_paid=Sum(Coalesce(F("amount_paid"), zero), filter=live, output_field=money),
        total_payable_balance=Sum("_balance", filter=live, output_field=money),
    )

    cent = Decimal("0.01")
    for key in ("total_spend", "total_ordered_value", "total_paid", "total_payable_balance"):
        totals[key] = Decimal(str(totals[key] or 0)).quantize(cent)
    return totals


def get_purchase_available_actions(purchase: Purchase) -> list[str]:
    """
    Returns contextual row actions for the Purchase Order list and detail dropdowns (UI Pages 5-6, 13-14).
    """
    actions = ["view"]
    if purchase.status == PurchaseStatusChoices.CANCELLED:
        return actions

    has_received = any(item.quantity_received > 0 for item in purchase.items.all())
    all_received = bool(purchase.items.all()) and all(
        item.quantity_received >= item.quantity_ordered for item in purchase.items.all()
    )

    if not has_received:
        actions.extend(["edit", "cancel", "delete"])

    if not all_received:
        actions.append("receive_goods")

    actions.append("record_purchase_invoice")

    has_active_invoice = hasattr(purchase, "purchase_invoices") and any(
        not inv.is_voided for inv in purchase.purchase_invoices.all()
    )
    if (has_received or has_active_invoice) and purchase.balance_due > Decimal("0.00"):
        actions.append("record_payment")

    if has_received:
        actions.append("create_return")

    return actions
