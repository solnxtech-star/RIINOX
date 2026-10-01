from django.db import transaction
from core.applications.sales.models import Sale, SaleItem, SaleReturn, SaleReturnItem

from core.applications.inventory.services import (
    update_inventory_for_sale,
    create_ledger_for_sale,
    update_inventory_for_return,
    create_ledger_for_return
)

def mock_create_invoice(sale: Sale):
    """Mock implementation for invoice creation."""
    pass

def mock_create_payment(sale: Sale):
    """Mock implementation for payment creation."""
    pass

def mock_create_receipt(sale: Sale):
    """Mock implementation for receipt creation."""
    pass

def mock_create_audit_event(sale: Sale):
    """Mock implementation for audit event creation."""
    pass

def create_sale(data: dict) -> Sale:
    """Create a Sale and its items, coordinating external side-effects."""
    items_data = data.pop('items', [])
    
    with transaction.atomic():
        sale = Sale.objects.create(**data)
        
        for item_data in items_data:
            SaleItem.objects.create(sale=sale, **item_data)
        
        # Execute external operations
        update_inventory_for_sale(sale)
        create_ledger_for_sale(sale)
        mock_create_invoice(sale)
        mock_create_payment(sale)
        mock_create_receipt(sale)
        mock_create_audit_event(sale)
        
    return sale

def create_sale_return(data: dict) -> SaleReturn:
    """Create a SaleReturn and its items."""
    items_data = data.pop('items', [])
    
    with transaction.atomic():
        sale_return = SaleReturn.objects.create(**data)
        
        for item_data in items_data:
            SaleReturnItem.objects.create(sale_return=sale_return, **item_data)
            
        update_inventory_for_return(sale_return)
        create_ledger_for_return(sale_return)
        mock_create_audit_event(sale_return.original_sale)
        
    return sale_return
