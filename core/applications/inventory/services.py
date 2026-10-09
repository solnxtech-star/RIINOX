import uuid
from django.db import transaction
from django.utils import timezone
from django.contrib.contenttypes.models import ContentType
from core.applications.inventory.models import Inventory, InventoryLedgerEntry, StockAdjustmentRequest
from core.applications.transactions.services import create_transaction, TransactionData      
from core.applications.notification.audit.services import record as audit_record
from core.applications.notification.audit.action import AuditAction
from core.helper.enums import (
    TransactionTypeChoices, 
    TransactionStatusChoices,
    RestockActionChoices,
    ApprovalStatusChoices,
    UsersRole,
    PhysicalCountStatusChoices,
    PurchaseStatusChoices,
)

from django.db.models import F

from rest_framework.exceptions import ValidationError
from core.applications.inventory.models import PhysicalStockCount
from core.applications.subscriptions.services import ensure_subscription_active

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

    ledger_entries = []
    
    for item in sale.items.all():
        remaining_qty = item.quantity * item.unit_multiplier
        
        # Order by expiry date (FIFO) to handle multiple batches safely
        inventory_records = Inventory.objects.select_for_update(of=('self',)).filter(
            product=item.product,
            variant=item.variant,
            warehouse=sale.location.warehouse
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
                warehouse=sale.location.warehouse
            )
            inv.quantity -= remaining_qty
            inv.save(update_fields=['quantity'])
            
            ledger_entries.append(InventoryLedgerEntry(
                transaction=transaction_record,
                product=item.product,
                variant=item.variant,
                batch=None,
                warehouse=sale.location.warehouse,
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
                    warehouse=sale.location.warehouse,
                    quantity_moved=-qty_to_deduct,
                    unit_cost=item.product.purchase_cost
                ))

    if ledger_entries:
        InventoryLedgerEntry.objects.bulk_create(ledger_entries)

    audit_record(
        action=AuditAction.SALES_ORDER_FULFILLED,
        organization=sale.organization,
        actor=sale.sales_rep,
        resource=sale,
        metadata={"total_items": len(ledger_entries)}
    )

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

@transaction.atomic
def create_opening_stock(product, variant, batch, warehouse, quantity, user):
    """
    Triggered during Product creation to log the initial stock.
    Creates an InventoryLedgerEntry and updates Inventory.
    """
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
def request_stock_adjustment(product, variant, batch, warehouse, quantity_change, adjustment_reason, notes, requested_unit, user):
    """
    Creates a StockAdjustmentRequest. Auto-approves if the user is an Admin/Owner.
    """
    ensure_subscription_active(product.organization)
    adjustment = StockAdjustmentRequest.objects.create(
        product=product,
        variant=variant,
        batch=batch,
        warehouse=warehouse,
        requested_quantity_change=quantity_change,
        requested_unit=requested_unit,
        adjustment_reason=adjustment_reason,
        notes=notes,
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
def single_transfer_stock(source_warehouse, destination_warehouse, product, variant, batch, quantity, user, note=None):
    """
    Atomically decreases source and increases destination via two ledger entries for a single product.
    """
    transaction_record = create_transaction(TransactionData(
        transaction_id=f"TRF-{uuid.uuid4().hex[:8].upper()}",
        organization=product.organization,
        transaction_type=TransactionTypeChoices.TRANSFER if hasattr(TransactionTypeChoices, 'TRANSFER') else 'TRANSFER',
        status=TransactionStatusChoices.COMPLETED,
        content_type=ContentType.objects.get_for_model(product),
        object_id=product.id,
        created_by=user,
        notes=note
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
def bulk_transfer_stock(source_warehouse, destination_warehouse, items, user, note=None, default_organization=None):
    """
    Atomically decreases source and increases destination for multiple items.
    `items` is a list of dicts: [{'product': p, 'variant': v, 'batch': b, 'quantity': q}]
    """
    if not items:
        return

    # Use the organization of the first product if not explicitly provided
    resolved_organization = default_organization or items[0]['product'].organization

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
    Updates PurchaseItem.quantity_received, creates Ledger Entries, and updates inventory stock.
    Transitions purchase status to partially_received or received.
    `items_received` is a list of dicts: [{'item_id': UUID, 'quantity_received': int}]
    """
    ensure_subscription_active(purchase.organization)

    if purchase.status == PurchaseStatusChoices.CANCELLED:
        raise ValidationError("Cannot receive goods against a cancelled purchase order.")

    transaction_record = create_transaction(TransactionData(
        transaction_id=f"RECV-{str(purchase.id)[:8]}-{uuid.uuid4().hex[:8].upper()}",
        organization=purchase.organization,
        transaction_type=TransactionTypeChoices.PURCHASE if hasattr(TransactionTypeChoices, 'PURCHASE') else 'PURCHASE',
        status=TransactionStatusChoices.COMPLETED,
        content_type=ContentType.objects.get_for_model(purchase),
        object_id=purchase.id,
        created_by=user
    ))

    entries = []
    for received_item_data in items_received:
        item = purchase.items.get(id=received_item_data['item_id'])
        received_quantity = received_item_data['quantity_received']

        if received_quantity <= 0:
            raise ValidationError(f"Received quantity must be greater than zero for '{item.product.name}'.")

        if item.quantity_received + received_quantity > item.quantity_ordered:
            raise ValidationError(
                f"Cannot receive {received_quantity} units of '{item.product.name}'. "
                f"Already received {item.quantity_received} of {item.quantity_ordered} ordered."
            )

        item.quantity_received += received_quantity
        item.save(update_fields=['quantity_received'])

        entries.append(InventoryLedgerEntry(
            transaction=transaction_record,
            product=item.product,
            variant=item.variant,
            batch=None,
            warehouse=purchase.warehouse,
            quantity_moved=received_quantity,
            unit_cost=item.purchase_cost
        ))
        
        inventory, _ = Inventory.objects.get_or_create(
            product=item.product, variant=item.variant, batch=None, warehouse=purchase.warehouse
        )
        inventory.quantity += received_quantity
        inventory.save(update_fields=['quantity'])
        
    InventoryLedgerEntry.objects.bulk_create(entries)

    # Transition purchase status and update received_by
    purchase.refresh_from_db()
    all_items = list(purchase.items.all())
    if all_items and all(it.quantity_received >= it.quantity_ordered for it in all_items):
        purchase.status = PurchaseStatusChoices.RECEIVED
    elif any(it.quantity_received > 0 for it in all_items):
        purchase.status = PurchaseStatusChoices.PARTIALLY_RECEIVED

    purchase.received_by = user
    purchase.save(update_fields=['status', 'received_by'])

    audit_record(
        action=AuditAction.PURCHASE_ORDER_RECEIVED,
        organization=purchase.organization,
        actor=user,
        resource=purchase,
        metadata={"items_received": len(items_received), "status": purchase.status}
    )
    return purchase

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
