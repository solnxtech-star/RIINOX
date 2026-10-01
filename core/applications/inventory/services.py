from django.db import transaction
from django.contrib.contenttypes.models import ContentType
from core.applications.inventory.models import Inventory, InventoryLedgerEntry
from core.applications.transactions.services import create_transaction, TransactionData
from core.helper.enums import TransactionTypeChoices, TransactionStatusChoices

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
    """Add stock back to the specific warehouse/location."""
    for item in sale_return.items.all():
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
