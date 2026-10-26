import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from core.applications.inventory.models import Inventory
from core.applications.inventory.models import InventoryLedgerEntry
from core.applications.inventory.models import PhysicalStockCount
from core.applications.inventory.models import StockAdjustmentRequest
from core.applications.notification.audit.action import AuditAction
from core.applications.notification.audit.services import record as audit_record
from core.applications.subscriptions.services.entitlements import (
    ensure_subscription_active,
)
from core.applications.transactions.services import TransactionData
from core.applications.transactions.services import create_transaction
from core.applications.transactions.services import (
    record_sale_cancellation_financial_entries,
)
from core.applications.transactions.services import record_sale_financial_entries
from core.applications.transactions.services import record_sale_return_financial_entries
from core.helper.enums import ApprovalStatusChoices
from core.helper.enums import PhysicalCountStatusChoices
from core.helper.enums import RestockActionChoices
from core.helper.enums import TransactionStatusChoices
from core.helper.enums import TransactionTypeChoices
from core.helper.enums import UsersRole


@dataclass(frozen=True, slots=True)
class StockTarget:
    """Identifies a specific product and optional variant/batch combination for inventory movements."""

    product: Any
    variant: Any = None
    batch: Any = None


@dataclass(frozen=True, slots=True)
class StockAdjustmentInput:
    """Parameter object carrying stock adjustment request details."""

    product: Any
    warehouse: Any
    quantity_change: int
    variant: Any = None
    batch: Any = None
    adjustment_reason: str | None = None
    notes: str | None = None
    requested_unit: str | None = None


@dataclass(frozen=True, slots=True)
class StockTransferInput:
    """Parameter object carrying single stock transfer details."""

    source_warehouse: Any
    destination_warehouse: Any
    product: Any
    quantity: int
    variant: Any = None
    batch: Any = None
    note: str | None = None


class InsufficientStock(Exception):
    def __init__(self, product, requested, available):
        self.product = product
        self.requested = requested
        self.available = available
        super().__init__(
            f"Out of stock: {product.name}. Requested {requested}, available {available}."
        )


@transaction.atomic
def process_sale_inventory_and_ledger(sale):
    """
    Deduct stock using FIFO across batches and create immutable ledger entries.
    Raises ValidationError if there is insufficient stock.
    """
    transaction_record = create_transaction(TransactionData(
        transaction_id=f"SALE-{sale.sale_id}"[:50],
        organization=sale.organization,
        transaction_type=TransactionTypeChoices.SALE,
        status=TransactionStatusChoices.COMPLETED,
        content_type=ContentType.objects.get_for_model(sale),
        object_id=sale.id,
        created_by=sale.sales_rep
    ))

    warehouse = sale.warehouse or (sale.location.warehouse if sale.location else None)
    if not warehouse:
        for item in sale.items.all():
            if item.product.track_inventory:
                raise ValidationError(f"No warehouse assigned to sale to deduct stock for {item.product.name}.")
        record_sale_financial_entries(sale, transaction_record)
        return transaction_record

    ledger_entries = []

    for item in sale.items.all():
        remaining_qty = item.quantity * item.unit_multiplier

        # Order by expiry date (FIFO) to handle multiple batches safely
        inventory_records = Inventory.objects.select_for_update(of=('self',)).filter(
            product=item.product,
            variant=item.variant,
            warehouse=warehouse,
        ).order_by(F('batch__expiry_date').asc(nulls_last=True), 'created_at')

        # Calculate total available stock across all batches
        total_available = sum(inv.quantity for inv in inventory_records)

        # We only block the sale if the product STRICTLY tracks inventory.
        if total_available < remaining_qty and item.product.track_inventory:
            raise ValidationError(
                f"Out of stock: {item.product.name}. "
                f"The cashier requested {remaining_qty}, but the system only has {total_available} available. "
                "Please restock or do a physical count."
            )

        if not item.product.track_inventory:
            # Untracked products (e.g. services) can freely go negative.
            # We don't need FIFO, just pick or create the default inventory record and deduct.
            inv, _ = Inventory.objects.get_or_create(
                product=item.product,
                variant=item.variant,
                batch=None,
                warehouse=warehouse,
            )
            inv.quantity -= remaining_qty
            inv.save(update_fields=['quantity'])

            ledger_entries.append(InventoryLedgerEntry(
                transaction=transaction_record,
                product=item.product,
                variant=item.variant,
                batch=None,
                warehouse=warehouse,
                quantity_moved=-remaining_qty,
                unit_cost=item.product.purchase_cost
            ))
            continue

        for inv in inventory_records:
            if remaining_qty <= 0:
                break

            qty_to_deduct = min(remaining_qty, inv.quantity)

            if qty_to_deduct > 0:
                inv.quantity -= qty_to_deduct
                inv.save(update_fields=['quantity'])
                remaining_qty -= qty_to_deduct

                ledger_entries.append(InventoryLedgerEntry(
                    transaction=transaction_record,
                    product=item.product,
                    variant=item.variant,
                    batch=inv.batch,
                    warehouse=warehouse,
                    quantity_moved=-qty_to_deduct,
                    unit_cost=item.product.purchase_cost
                ))

    if ledger_entries:
        InventoryLedgerEntry.objects.bulk_create(ledger_entries)

    record_sale_financial_entries(sale, transaction_record)

    audit_record(
        action=AuditAction.SALES_ORDER_FULFILLED,
        organization=sale.organization,
        actor=sale.sales_rep,
        resource=sale,
        metadata={"total_items": len(ledger_entries)}
    )
    return transaction_record


@transaction.atomic
def process_return_inventory_and_ledger(sale_return):
    """
    Add stock back and create ledger entries, safely handling batch uncertainties.
    """
    transaction_record = create_transaction(TransactionData(
        transaction_id=f"RET-{sale_return.id}"[:50],
        organization=sale_return.organization,
        transaction_type=TransactionTypeChoices.RETURN,
        status=TransactionStatusChoices.COMPLETED,
        content_type=ContentType.objects.get_for_model(sale_return),
        object_id=sale_return.id,
        created_by=sale_return.original_sale.sales_rep
    ))

    entries = []
    for item in sale_return.items.all():
        if item.restock_action == RestockActionChoices.RETURN_TO_STOCK:
            return_qty_in_base_units = item.quantity * item.sale_item.unit_multiplier

            # Without a specific batch_id on SaleReturnItem, we restock to the generic pool
            inventory, _ = Inventory.objects.select_for_update().get_or_create(
                product=item.sale_item.product,
                variant=item.sale_item.variant,
                batch=None,
                warehouse=sale_return.original_sale.location.warehouse
            )
            inventory.quantity += return_qty_in_base_units
            inventory.save(update_fields=['quantity'])

            entries.append(InventoryLedgerEntry(
                transaction=transaction_record,
                product=item.sale_item.product,
                variant=item.sale_item.variant,
                batch=None,
                warehouse=sale_return.original_sale.location.warehouse,
                quantity_moved=return_qty_in_base_units,
                unit_cost=item.sale_item.product.purchase_cost
            ))

    if entries:
        InventoryLedgerEntry.objects.bulk_create(entries)

    record_sale_return_financial_entries(sale_return, transaction_record)
    return transaction_record


@transaction.atomic
def reverse_sale_inventory_and_ledger(sale, *, actor=None, reason: str = ""):
    """
    Reverse the stock movement of a cancelled sale and record ledger transactions (PRD §44).
    Restocks to warehouse inventory and logs positive movement.
    """
    transaction_record = create_transaction(TransactionData(
        transaction_id=f"CNL-{sale.sale_id}"[:50],
        organization=sale.organization,
        transaction_type=TransactionTypeChoices.RETURN,
        status=TransactionStatusChoices.COMPLETED,
        content_type=ContentType.objects.get_for_model(sale),
        object_id=sale.id,
        created_by=actor or sale.sales_rep,
    ))

    warehouse = sale.warehouse or (sale.location.warehouse if sale.location else None)
    if not warehouse:
        record_sale_cancellation_financial_entries(sale, transaction_record)
        return transaction_record

    entries = []
    for item in sale.items.all():
        qty_to_restore = item.quantity * item.unit_multiplier
        inventory, _ = Inventory.objects.select_for_update().get_or_create(
            product=item.product,
            variant=item.variant,
            warehouse=warehouse,
            batch=None,
            defaults={"quantity": 0},
        )
        inventory.quantity += qty_to_restore
        inventory.save(update_fields=["quantity"])

        entries.append(InventoryLedgerEntry(
            transaction=transaction_record,
            product=item.product,
            variant=item.variant,
            batch=None,
            warehouse=warehouse,
            quantity_moved=qty_to_restore,
            unit_cost=item.product.purchase_cost,
        ))

    if entries:
        InventoryLedgerEntry.objects.bulk_create(entries)

    record_sale_cancellation_financial_entries(sale, transaction_record)
    return transaction_record


@transaction.atomic
def create_opening_stock(
    target: StockTarget | Any = None,
    warehouse: Any = None,
    quantity: int = 0,
    user: Any = None,
    *legacy_args: Any,
    **legacy_kwargs: Any,
):
    """
    Triggered during Product creation to log the initial stock.
    Creates an InventoryLedgerEntry and updates Inventory.
    Organization and unit_cost are derived from `target.product`.
    """
    if isinstance(target, StockTarget):
        product = target.product
        variant = target.variant
        batch = target.batch
    elif legacy_args:
        product = target
        variant = warehouse
        batch = quantity if quantity != 0 or len(legacy_args) >= 1 else None
        warehouse = user
        quantity = legacy_args[0]
        user = legacy_args[1] if len(legacy_args) > 1 else legacy_kwargs.get("user")
    else:
        product = target if target is not None else legacy_kwargs.get("product")
        variant = legacy_kwargs.get("variant")
        batch = legacy_kwargs.get("batch")
        warehouse = legacy_kwargs.get("warehouse", warehouse)
        quantity = legacy_kwargs.get("quantity", quantity)
        user = legacy_kwargs.get("user", user)

    transaction_record = create_transaction(TransactionData(
        transaction_id=f"OPEN-{uuid.uuid4().hex[:8].upper()}",
        organization=product.organization,
        transaction_type=TransactionTypeChoices.OPENING_STOCK,
        status=TransactionStatusChoices.COMPLETED,
        content_type=ContentType.objects.get_for_model(product),
        object_id=product.id,
        created_by=user
    ))

    InventoryLedgerEntry.objects.create(
        transaction=transaction_record,
        product=product,
        variant=variant,
        batch=batch,
        warehouse=warehouse,
        quantity_moved=quantity,
        unit_cost=product.purchase_cost
    )

    inventory, _ = Inventory.objects.get_or_create(
        product=product, variant=variant, batch=batch, warehouse=warehouse
    )
    inventory.quantity = quantity
    inventory.save(update_fields=['quantity'])

    audit_record(
        action=AuditAction.INVENTORY_OPENING_STOCK_SET,
        organization=product.organization,
        actor=user,
        resource=product,
        metadata={"quantity_change": quantity, "warehouse_id": str(warehouse.id)}
    )


@transaction.atomic
def request_stock_adjustment(
    payload: StockAdjustmentInput | Any = None,
    *,
    user: Any = None,
    **legacy_kwargs: Any,
):
    """
    Creates a StockAdjustmentRequest. Auto-approves if the user is an Admin/Owner.
    Organization is derived from `payload.product.organization`.
    """
    if isinstance(payload, StockAdjustmentInput):
        resolved = payload
    else:
        resolved = StockAdjustmentInput(
            product=payload if payload is not None else legacy_kwargs.get("product"),
            variant=legacy_kwargs.get("variant"),
            batch=legacy_kwargs.get("batch"),
            warehouse=legacy_kwargs.get("warehouse"),
            quantity_change=legacy_kwargs.get("quantity_change", 0),
            adjustment_reason=legacy_kwargs.get("adjustment_reason"),
            notes=legacy_kwargs.get("notes"),
            requested_unit=legacy_kwargs.get("requested_unit"),
        )

    ensure_subscription_active(resolved.product.organization)
    adjustment = StockAdjustmentRequest.objects.create(
        product=resolved.product,
        variant=resolved.variant,
        batch=resolved.batch,
        warehouse=resolved.warehouse,
        requested_quantity_change=resolved.quantity_change,
        requested_unit=resolved.requested_unit,
        adjustment_reason=resolved.adjustment_reason,
        notes=resolved.notes,
        requested_by=user,
        status=ApprovalStatusChoices.PENDING
    )

    is_privileged = user.is_superuser or getattr(user, "role", None) in (UsersRole.OWNER, UsersRole.ADMIN)

    if is_privileged:
        approve_stock_adjustment(adjustment.id, admin_user=user)
        adjustment.refresh_from_db()

    return adjustment


@transaction.atomic
def approve_stock_adjustment(adjustment_id, admin_user):
    """
    Approves a request, updates inventory, and creates a Ledger Entry.
    """
    adjustment = StockAdjustmentRequest.objects.select_related('product', 'warehouse').get(id=adjustment_id)
    if adjustment.status != ApprovalStatusChoices.PENDING:
        raise ValueError("Only PENDING requests can be approved.")

    adjustment.status = ApprovalStatusChoices.APPROVED
    adjustment.reviewed_by = admin_user
    adjustment.reviewed_at = timezone.now()
    adjustment.save(update_fields=['status', 'reviewed_by', 'reviewed_at'])

    # Create Ledger
    transaction_record = create_transaction(TransactionData(
        transaction_id=f"ADJ-{adjustment.id}",
        organization=adjustment.product.organization,
        transaction_type=TransactionTypeChoices.ADJUSTMENT if hasattr(TransactionTypeChoices, 'ADJUSTMENT') else 'ADJUSTMENT',
        status=TransactionStatusChoices.COMPLETED,
        content_type=ContentType.objects.get_for_model(adjustment),
        object_id=adjustment.id,
        created_by=adjustment.requested_by
    ))

    InventoryLedgerEntry.objects.create(
        transaction=transaction_record,
        product=adjustment.product,
        variant=adjustment.variant,
        batch=adjustment.batch,
        warehouse=adjustment.warehouse,
        quantity_moved=adjustment.requested_quantity_change,
        unit_cost=adjustment.product.purchase_cost
    )

    # Update Inventory
    inventory, _ = Inventory.objects.get_or_create(
        product=adjustment.product, variant=adjustment.variant, batch=adjustment.batch, warehouse=adjustment.warehouse
    )
    inventory.quantity += adjustment.requested_quantity_change
    inventory.save(update_fields=['quantity'])

    audit_record(
        action=AuditAction.INVENTORY_ADJUSTED,
        organization=adjustment.product.organization,
        actor=admin_user,
        resource=adjustment.product,
        reason=adjustment.adjustment_reason or "Stock Adjustment Approved",
        metadata={"quantity_change": adjustment.requested_quantity_change, "warehouse_id": str(adjustment.warehouse.id)}
    )


@transaction.atomic
def single_transfer_stock(
    payload: StockTransferInput | Any = None,
    *legacy_args: Any,
    user: Any = None,
    **legacy_kwargs: Any,
):
    """
    Atomically decreases source and increases destination via two ledger entries for a single product.
    Organization and unit_cost are derived from `payload.product`.
    """
    if isinstance(payload, StockTransferInput):
        resolved = payload
    elif legacy_args:
        dest_wh, prod, var, bch, qty, *rest = legacy_args
        legacy_user = rest[0] if rest else None
        legacy_note = rest[1] if len(rest) > 1 else legacy_kwargs.get("note")
        resolved = StockTransferInput(
            source_warehouse=payload,
            destination_warehouse=dest_wh,
            product=prod,
            variant=var,
            batch=bch,
            quantity=qty,
            note=legacy_note,
        )
        if user is None:
            user = legacy_user
    else:
        resolved = StockTransferInput(
            source_warehouse=payload if payload is not None else legacy_kwargs.get("source_warehouse"),
            destination_warehouse=legacy_kwargs.get("destination_warehouse"),
            product=legacy_kwargs.get("product"),
            variant=legacy_kwargs.get("variant"),
            batch=legacy_kwargs.get("batch"),
            quantity=legacy_kwargs.get("quantity", 0),
            note=legacy_kwargs.get("note"),
        )

    product = resolved.product
    variant = resolved.variant
    batch = resolved.batch
    source_warehouse = resolved.source_warehouse
    destination_warehouse = resolved.destination_warehouse
    quantity = resolved.quantity

    transaction_record = create_transaction(TransactionData(
        transaction_id=f"TRF-{uuid.uuid4().hex[:8].upper()}",
        organization=product.organization,
        transaction_type=TransactionTypeChoices.TRANSFER if hasattr(TransactionTypeChoices, 'TRANSFER') else 'TRANSFER',
        status=TransactionStatusChoices.COMPLETED,
        content_type=ContentType.objects.get_for_model(product),
        object_id=product.id,
        created_by=user,
        notes=resolved.note
    ))

    # Source Decrease
    InventoryLedgerEntry.objects.create(
        transaction=transaction_record,
        product=product, variant=variant, batch=batch, warehouse=source_warehouse,
        quantity_moved=-quantity,
        unit_cost=product.purchase_cost
    )
    source_inventory = Inventory.objects.select_for_update().get(
        product=product, variant=variant, batch=batch, warehouse=source_warehouse
    )
    source_inventory.quantity -= quantity
    source_inventory.save(update_fields=['quantity'])

    # Destination Increase
    InventoryLedgerEntry.objects.create(
        transaction=transaction_record,
        product=product, variant=variant, batch=batch, warehouse=destination_warehouse,
        quantity_moved=quantity,
        unit_cost=product.purchase_cost
    )
    destination_inventory, _ = Inventory.objects.get_or_create(
        product=product, variant=variant, batch=batch, warehouse=destination_warehouse
    )
    destination_inventory.quantity += quantity
    destination_inventory.save(update_fields=['quantity'])


@transaction.atomic
def bulk_transfer_stock(source_warehouse, destination_warehouse, items, user, note=None):
    """
    Atomically decreases source and increases destination for multiple items.
    `items` is a list of dicts: [{'product': p, 'variant': v, 'batch': b, 'quantity': q}]
    Organization is derived from `source_warehouse.organization` or `items[0]['product'].organization`.
    """
    if not items:
        return

    resolved_organization = getattr(source_warehouse, "organization", None) or items[0]['product'].organization

    transaction_record = create_transaction(TransactionData(
        transaction_id=f"TRF-BLK-{uuid.uuid4().hex[:8].upper()}",
        organization=resolved_organization,
        transaction_type=TransactionTypeChoices.TRANSFER if hasattr(TransactionTypeChoices, 'TRANSFER') else 'TRANSFER',
        status=TransactionStatusChoices.COMPLETED,
        content_type=ContentType.objects.get_for_model(source_warehouse), # Attach to source warehouse since it's bulk
        object_id=source_warehouse.id,
        created_by=user,
        notes=note
    ))

    ledger_entries = []

    for item in items:
        product = item['product']
        variant = item.get('variant')
        batch = item.get('batch')
        quantity = item['quantity']

        # Source Decrease Ledger Entry
        ledger_entries.append(
            InventoryLedgerEntry(
                transaction=transaction_record,
                product=product, variant=variant, batch=batch, warehouse=source_warehouse,
                quantity_moved=-quantity,
                unit_cost=product.purchase_cost
            )
        )
        # Source Inventory Update
        source_inventory = Inventory.objects.select_for_update().get(
            product=product, variant=variant, batch=batch, warehouse=source_warehouse
        )
        source_inventory.quantity -= quantity
        source_inventory.save(update_fields=['quantity'])

        # Destination Increase Ledger Entry
        ledger_entries.append(
            InventoryLedgerEntry(
                transaction=transaction_record,
                product=product, variant=variant, batch=batch, warehouse=destination_warehouse,
                quantity_moved=quantity,
                unit_cost=product.purchase_cost
            )
        )
        # Destination Inventory Update
        destination_inventory, _ = Inventory.objects.get_or_create(
            product=product, variant=variant, batch=batch, warehouse=destination_warehouse
        )
        destination_inventory.quantity += quantity
        destination_inventory.save(update_fields=['quantity'])

    InventoryLedgerEntry.objects.bulk_create(ledger_entries)


@transaction.atomic
def receive_purchase_order(purchase, items_received, user):
    """
    Delegates purchase order receipt to `purchase.services.receive_purchase_order`
    so unit conversions, batch tracking, and financial ledger entries are always applied.
    """
    from core.applications.purchase.services import (
        receive_purchase_order as purchase_receive_service,
    )

    return purchase_receive_service(
        purchase=purchase,
        items_received=items_received,
        user=user,
    )


@transaction.atomic
def finalize_physical_stock_count(count_id, admin_user):
    """
    Finalizes a DRAFT physical stock count.
    For each item with a variance, updates the Inventory quantity directly and
    records an InventoryLedgerEntry of type CORRECTION to document the variance.
    """
    count = PhysicalStockCount.objects.prefetch_related('items__product', 'warehouse__organization').get(id=count_id)
    if count.status != PhysicalCountStatusChoices.DRAFT:
        raise ValidationError("Only DRAFT stock counts can be finalized.")

    transaction_record = create_transaction(TransactionData(
        transaction_id=f"COUNT-{count.id}"[:50],
        organization=count.warehouse.organization,
        transaction_type=TransactionTypeChoices.STOCK_ADJUSTMENT,
        status=TransactionStatusChoices.COMPLETED,
        content_type=ContentType.objects.get_for_model(count),
        object_id=count.id,
        created_by=admin_user
    ))

    ledger_entries = []

    for item in count.items.all():
        variance = item.counted_quantity - item.expected_quantity
        if variance == 0:
            continue

        inv, _ = Inventory.objects.select_for_update().get_or_create(
            product=item.product,
            variant=item.variant,
            batch=None,
            warehouse=count.warehouse
        )

        inv.quantity += variance
        inv.save(update_fields=['quantity'])

        ledger_entries.append(InventoryLedgerEntry(
            transaction=transaction_record,
            product=item.product,
            variant=item.variant,
            batch=None,
            warehouse=count.warehouse,
            quantity_moved=variance,
            unit_cost=item.product.purchase_cost
        ))

    if ledger_entries:
        InventoryLedgerEntry.objects.bulk_create(ledger_entries)

    count.status = PhysicalCountStatusChoices.COMPLETED
    count.save(update_fields=['status'])
    return count


@transaction.atomic
def process_invoice_stock_deduction(invoice, lines, *, actor=None):
    """
    Deduct stock for a confirmed standalone invoice, FIFO across batches.

    `lines` is a sequence of (product, variant_id, quantity) with quantity in
    BASE units, already sorted so rows lock in a fixed order (PRD §43).
    Untracked products are skipped: they have no stock to move.
    """
    tracked = [line for line in lines if line[0].track_inventory]
    if not tracked:
        return None

    warehouse = invoice.warehouse
    transaction_record = create_transaction(TransactionData(
        transaction_id=f"INV-{invoice.invoice_number}"[:50],
        organization=invoice.organization,
        transaction_type=TransactionTypeChoices.SALE,
        status=TransactionStatusChoices.COMPLETED,
        content_type=ContentType.objects.get_for_model(invoice),
        object_id=invoice.pk,
        created_by=actor,
    ))

    entries = []
    for product, variant_id, quantity in tracked:
        remaining = quantity
        records = list(
            Inventory.objects.select_for_update(of=("self",))
            .filter(product=product, variant_id=variant_id, warehouse=warehouse)
            .order_by(F("batch__expiry_date").asc(nulls_last=True), "created_at")
        )
        available = sum(r.quantity for r in records)
        if available < remaining:
            raise InsufficientStock(product, remaining, available)

        for inv in records:
            if remaining <= 0:
                break
            take = min(remaining, inv.quantity)
            if take <= 0:
                continue
            inv.quantity -= take
            inv.save(update_fields=["quantity"])
            remaining -= take
            entries.append(InventoryLedgerEntry(
                transaction=transaction_record,
                product=product,
                variant_id=variant_id,
                batch=inv.batch,
                warehouse=warehouse,
                quantity_moved=-take,
                unit_cost=product.purchase_cost,
            ))

    InventoryLedgerEntry.objects.bulk_create(entries)
    return transaction_record


@transaction.atomic
def reverse_invoice_stock_deduction(invoice, *, actor=None, reason: str = ""):
    """
    Reverse a standalone invoice's deduction exactly: same products, variants,
    batches and warehouse, read back from the original ledger entries (PRD §44).
    """
    deductions = list(
        InventoryLedgerEntry.objects.filter(
            transaction__organization=invoice.organization,
            transaction__content_type=ContentType.objects.get_for_model(invoice),
            transaction__object_id=invoice.pk,
            transaction__transaction_type=TransactionTypeChoices.SALE,
            quantity_moved__lt=0,
        ).order_by("product_id", "variant_id", "batch_id")
    )
    if not deductions:
        return None

    transaction_record = create_transaction(TransactionData(
        transaction_id=f"VOID-{invoice.invoice_number}"[:50],
        organization=invoice.organization,
        transaction_type=TransactionTypeChoices.RETURN,
        status=TransactionStatusChoices.COMPLETED,
        content_type=ContentType.objects.get_for_model(invoice),
        object_id=invoice.pk,
        created_by=actor,
        notes=reason or None,
    ))

    entries = []
    for entry in deductions:
        restored = -entry.quantity_moved
        inventory, _ = Inventory.objects.select_for_update().get_or_create(
            product=entry.product,
            variant=entry.variant,
            batch=entry.batch,
            warehouse=entry.warehouse,
            defaults={"quantity": 0},
        )
        inventory.quantity += restored
        inventory.save(update_fields=["quantity"])
        entries.append(InventoryLedgerEntry(
            transaction=transaction_record,
            product=entry.product,
            variant=entry.variant,
            batch=entry.batch,
            warehouse=entry.warehouse,
            quantity_moved=restored,
            unit_cost=entry.unit_cost,
        ))

    InventoryLedgerEntry.objects.bulk_create(entries)
    return transaction_record


@transaction.atomic
def record_purchase_receipt_inventory_and_ledger(
    *,
    purchase: Any,
    movement_lines: list[dict[str, Any]],
    received_cost_total: Decimal,
    user: Any,
):
    """
    Records inventory stock additions, InventoryLedgerEntry rows, and double-entry FinancialLedgerEntry
    rows for a Purchase Order goods receipt (PRD §16, §18, §42-43).
    """
    from core.applications.transactions.services import record_purchase_financial_entries

    ensure_subscription_active(purchase.organization)

    transaction_record = create_transaction(
        TransactionData(
            transaction_id=f"RECV-{str(purchase.id)[:8]}-{uuid.uuid4().hex[:8].upper()}",
            organization=purchase.organization,
            transaction_type=TransactionTypeChoices.PURCHASE,
            status=TransactionStatusChoices.COMPLETED,
            content_type=ContentType.objects.get_for_model(purchase),
            object_id=purchase.id,
            created_by=user,
            notes=f"Goods received for purchase {purchase.purchase_number}",
        )
    )

    sorted_lines = sorted(
        movement_lines,
        key=lambda line: (
            str(line["product"].pk),
            str(line["variant"].pk if line.get("variant") else ""),
            str(line["batch"].pk if line.get("batch") else ""),
        ),
    )

    entries = []
    for line in sorted_lines:
        product = line["product"]
        variant = line.get("variant")
        batch = line.get("batch")
        quantity_moved = int(line["base_quantity_moved"])
        unit_cost = line["base_unit_cost"]

        entries.append(
            InventoryLedgerEntry(
                transaction=transaction_record,
                product=product,
                variant=variant,
                batch=batch,
                warehouse=purchase.warehouse,
                quantity_moved=quantity_moved,
                unit_cost=unit_cost,
            )
        )

        inventory, _ = Inventory.objects.select_for_update().get_or_create(
            product=product,
            variant=variant,
            batch=batch,
            warehouse=purchase.warehouse,
            defaults={"quantity": 0},
        )
        inventory.quantity += quantity_moved
        inventory.save(update_fields=["quantity"])

    if entries:
        InventoryLedgerEntry.objects.bulk_create(entries)

    if received_cost_total > Decimal("0.00"):
        record_purchase_financial_entries(purchase, transaction_record, received_cost_total)

    return transaction_record


@transaction.atomic
def process_purchase_return_inventory_and_ledger(
    purchase_return: Any,
    *,
    actor: Any = None,
):
    """
    Deducts returned supplier goods from warehouse inventory under select_for_update()
    and writes negative InventoryLedgerEntry + double-entry FinancialLedgerEntry rows.
    """
    from core.applications.transactions.services import record_purchase_return_financial_entries

    purchase = purchase_return.purchase
    ensure_subscription_active(purchase.organization)

    transaction_record = create_transaction(
        TransactionData(
            transaction_id=f"PRET-{str(purchase_return.id)[:8]}-{uuid.uuid4().hex[:6].upper()}",
            organization=purchase.organization,
            transaction_type=TransactionTypeChoices.RETURN,
            status=TransactionStatusChoices.COMPLETED,
            content_type=ContentType.objects.get_for_model(purchase_return),
            object_id=purchase_return.id,
            created_by=actor or purchase_return.returned_by,
            notes=purchase_return.reason or f"Supplier return for {purchase.purchase_number}",
        )
    )

    return_items = list(
        purchase_return.items.select_related(
            "purchase_item__product",
            "purchase_item__variant",
            "purchase_item__unit",
            "batch",
        ).order_by("purchase_item__product_id", "pk")
    )

    entries = []
    for return_item in return_items:
        p_item = return_item.purchase_item
        product = p_item.product
        variant = p_item.variant
        batch = return_item.batch
        multiplier = Decimal(str(p_item.unit.multiplier)) if p_item.unit else Decimal("1.000")
        base_qty = int((Decimal(str(return_item.quantity)) * multiplier).to_integral_value())

        inventory_qs = Inventory.objects.select_for_update().filter(
            product=product,
            variant=variant,
            warehouse=purchase.warehouse,
        )
        if batch is not None:
            inventory_qs = inventory_qs.filter(batch=batch)
        records = list(inventory_qs.order_by("created_at"))
        available = sum(r.quantity for r in records)
        if available < base_qty:
            raise InsufficientStock(product, base_qty, available)

        remaining = base_qty
        base_unit_cost = (Decimal(str(p_item.purchase_cost)) / multiplier).quantize(Decimal("0.01"))
        for inv in records:
            if remaining <= 0:
                break
            take = min(remaining, inv.quantity)
            if take <= 0:
                continue
            inv.quantity -= take
            inv.save(update_fields=["quantity"])
            remaining -= take
            entries.append(
                InventoryLedgerEntry(
                    transaction=transaction_record,
                    product=product,
                    variant=variant,
                    batch=inv.batch,
                    warehouse=purchase.warehouse,
                    quantity_moved=-take,
                    unit_cost=base_unit_cost,
                )
            )

    if entries:
        InventoryLedgerEntry.objects.bulk_create(entries)

    record_purchase_return_financial_entries(purchase_return, transaction_record)
    return transaction_record


def get_purchase_stock_activity(purchase: Any):
    """
    Returns InventoryLedgerEntry records for a Purchase Order and any of its PurchaseReturns
    to power the Stock Activity detail tab (UI Page 7).
    """
    purchase_ct = ContentType.objects.get_for_model(purchase)
    purchase_ids = {str(purchase.pk), str(purchase.pk).replace("-", "")}
    try:
        purchase_ids.add(str(uuid.UUID(str(purchase.pk))))
    except ValueError:
        pass
    query = Q(transaction__content_type=purchase_ct, transaction__object_id__in=purchase_ids)

    if hasattr(purchase, "returns"):
        return_ids: set[str] = set()
        for pk in purchase.returns.values_list("pk", flat=True):
            return_ids.add(str(pk))
            return_ids.add(str(pk).replace("-", ""))
            try:
                return_ids.add(str(uuid.UUID(str(pk))))
            except ValueError:
                pass
        if return_ids:
            return_model = purchase.returns.model
            return_ct = ContentType.objects.get_for_model(return_model)
            query |= Q(transaction__content_type=return_ct, transaction__object_id__in=return_ids)

    return (
        InventoryLedgerEntry.objects.filter(
            transaction__organization=purchase.organization,
        )
        .filter(query)
        .select_related("transaction", "product", "variant", "batch", "warehouse")
        .order_by("-created_at")
    )
