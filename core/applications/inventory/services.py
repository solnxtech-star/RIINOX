import uuid
from django.db import transaction
from django.utils import timezone
from django.contrib.contenttypes.models import ContentType
from core.applications.inventory.models import Inventory, InventoryLedgerEntry, StockAdjustmentRequest
from core.applications.transactions.services import create_transaction, TransactionData      
from core.helper.enums import (
    TransactionTypeChoices, 
    TransactionStatusChoices,
    RestockActionChoices,
    ApprovalStatusChoices,
    UsersRole
)

def update_inventory_for_sale(sale):
    """Deduct stock from the specific warehouse/location."""
    for item in sale.items.all():
        inventory = Inventory.objects.select_for_update().get(
            product=item.product,
            variant=item.variant,
            warehouse=sale.location.warehouse
        )
        inventory.quantity -= item.quantity
        inventory.save(update_fields=['quantity'])

def create_ledger_for_sale(sale):
    """Create a Transaction and immutable ledger entries for the sale."""
    trx = create_transaction(TransactionData(
        transaction_id=f"SALE-{sale.sale_id}"[:50],
        organization=sale.organization,
        transaction_type=TransactionTypeChoices.SALE,
        status=TransactionStatusChoices.COMPLETED,
        content_type=ContentType.objects.get_for_model(sale),
        object_id=sale.id,
        created_by=sale.sales_rep
    ))
    
    entries = []
    for item in sale.items.all():
        entries.append(InventoryLedgerEntry(
            transaction=trx,
            product=item.product,
            variant=item.variant,
            warehouse=sale.location.warehouse,
            quantity_moved=-item.quantity,
            unit_cost=item.product.purchase_cost
        ))
    InventoryLedgerEntry.objects.bulk_create(entries)

def update_inventory_for_return(sale_return):
    """Add stock back to the specific warehouse/location only if returning to stock."""
    for item in sale_return.items.all():
        if item.restock_action == RestockActionChoices.RETURN_TO_STOCK:
            inventory = Inventory.objects.select_for_update().get(
                product=item.sale_item.product,
                variant=item.sale_item.variant,
                warehouse=sale_return.original_sale.location.warehouse
            )
            inventory.quantity += item.quantity
            inventory.save(update_fields=['quantity'])

def create_ledger_for_return(sale_return):
    """Create a Transaction and immutable ledger entries for the return."""
    trx = create_transaction(TransactionData(
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
        entries.append(InventoryLedgerEntry(
            transaction=trx,
            product=item.sale_item.product,
            variant=item.sale_item.variant,
            warehouse=sale_return.original_sale.location.warehouse,
            quantity_moved=item.quantity,
            unit_cost=item.sale_item.product.purchase_cost
        ))
    InventoryLedgerEntry.objects.bulk_create(entries)

@transaction.atomic
def create_opening_stock(product, variant, batch, warehouse, quantity, user):
    """
    Triggered during Product creation to log the initial stock.
    Creates an InventoryLedgerEntry and updates Inventory.
    """
    trx = create_transaction(TransactionData(
        transaction_id=f"OPEN-{uuid.uuid4().hex[:8].upper()}",
        organization=product.organization,
        transaction_type=TransactionTypeChoices.OPENING_STOCK,
        status=TransactionStatusChoices.COMPLETED,
        content_type=ContentType.objects.get_for_model(product),
        object_id=product.id,
        created_by=user
    ))

    InventoryLedgerEntry.objects.create(
        transaction=trx,
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


@transaction.atomic
def request_stock_adjustment(product, variant, batch, warehouse, quantity_change, reason, user):
    """
    Creates a StockAdjustmentRequest. Auto-approves if the user is an Admin/Owner.
    """
    adjustment = StockAdjustmentRequest.objects.create(
        product=product,
        variant=variant,
        batch=batch,
        warehouse=warehouse,
        requested_quantity_change=quantity_change,
        reason=reason,
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
    trx = create_transaction(TransactionData(
        transaction_id=f"ADJ-{adjustment.id}",
        organization=adjustment.product.organization,
        transaction_type=TransactionTypeChoices.ADJUSTMENT if hasattr(TransactionTypeChoices, 'ADJUSTMENT') else 'ADJUSTMENT',
        status=TransactionStatusChoices.COMPLETED,
        content_type=ContentType.objects.get_for_model(adjustment),
        object_id=adjustment.id,
        created_by=adjustment.requested_by
    ))

    InventoryLedgerEntry.objects.create(
        transaction=trx,
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


@transaction.atomic
def transfer_stock(source_wh, dest_wh, product, variant, batch, quantity, user):
    """
    Atomically decreases source and increases destination via two ledger entries.
    """
    trx = create_transaction(TransactionData(
        transaction_id=f"TRF-{uuid.uuid4().hex[:8].upper()}",
        organization=product.organization,
        transaction_type=TransactionTypeChoices.TRANSFER if hasattr(TransactionTypeChoices, 'TRANSFER') else 'TRANSFER',
        status=TransactionStatusChoices.COMPLETED,
        content_type=ContentType.objects.get_for_model(product),
        object_id=product.id,
        created_by=user
    ))

    # Source Decrease
    InventoryLedgerEntry.objects.create(
        transaction=trx,
        product=product, variant=variant, batch=batch, warehouse=source_wh,
        quantity_moved=-quantity,
        unit_cost=product.purchase_cost
    )
    source_inv = Inventory.objects.select_for_update().get(
        product=product, variant=variant, batch=batch, warehouse=source_wh
    )
    source_inv.quantity -= quantity
    source_inv.save(update_fields=['quantity'])

    # Destination Increase
    InventoryLedgerEntry.objects.create(
        transaction=trx,
        product=product, variant=variant, batch=batch, warehouse=dest_wh,
        quantity_moved=quantity,
        unit_cost=product.purchase_cost
    )
    destination_inventory, _ = Inventory.objects.get_or_create(
        product=product, variant=variant, batch=batch, warehouse=dest_wh
    )
    destination_inventory.quantity += quantity
    destination_inventory.save(update_fields=['quantity'])


@transaction.atomic
def receive_purchase_order(purchase, items_received, user):
    """
    Updates PurchaseItem.quantity_received and creates Ledger Entries.
    `items_received` is a list of dicts: [{'item_id': 1, 'quantity_received': 50}]
    """
    trx = create_transaction(TransactionData(
        transaction_id=f"RECV-{purchase.id}-{user.id}"[:50],
        organization=purchase.organization,
        transaction_type=TransactionTypeChoices.PURCHASE if hasattr(TransactionTypeChoices, 'PURCHASE') else 'PURCHASE',
        status=TransactionStatusChoices.COMPLETED,
        content_type=ContentType.objects.get_for_model(purchase),
        object_id=purchase.id,
        created_by=user
    ))

    entries = []
    for recv_data in items_received:
        item = purchase.items.get(id=recv_data['item_id'])
        qty = recv_data['quantity_received']

        item.quantity_received += qty
        item.save(update_fields=['quantity_received'])

        entries.append(InventoryLedgerEntry(
            transaction=trx,
            product=item.product,
            variant=None, # Update if PO supports variants
            batch=None,   # Update if PO supports batches
            warehouse=purchase.warehouse,
            quantity_moved=qty,
            unit_cost=item.purchase_cost
        ))
        
        inventory, _ = Inventory.objects.get_or_create(
            product=item.product, variant=None, batch=None, warehouse=purchase.warehouse
        )
        inventory.quantity += qty
        inventory.save(update_fields=['quantity'])
        
    InventoryLedgerEntry.objects.bulk_create(entries)
