from django.db import transaction
from core.applications.sales.models import Sale, SaleItem, SaleReturn, SaleReturnItem

from core.applications.inventory.services import (
    process_sale_inventory_and_ledger,
    process_return_inventory_and_ledger
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
    """Real implementation for audit event creation."""
    from core.applications.notification.audit.services import record as audit_record
    from core.applications.notification.audit.action import AuditAction
    
    audit_record(
        action=AuditAction.SALES_ORDER_CREATED,
        organization=sale.organization,
        actor=sale.sales_rep,
        resource=sale,
        metadata={"total_amount": str(sale.total_amount)} if hasattr(sale, 'total_amount') else {}
    )

def create_sale(data: dict) -> Sale:
    """Create a Sale and its items, coordinating external side-effects."""
    items_data = data.pop('items', [])
    
    with transaction.atomic():
        sale = Sale.objects.create(**data)
        
        for item_data in items_data:
            product = item_data['product']
            unit_name = item_data.get('unit_name')
            
            if unit_name and unit_name != product.unit_of_measurement:
                conversion = product.unit_conversions.filter(unit_name=unit_name).first()
                if conversion:
                    item_data['unit_multiplier'] = conversion.multiplier
                else:
                    item_data['unit_multiplier'] = 1.0
            else:
                item_data['unit_multiplier'] = 1.0
                
            SaleItem.objects.create(sale=sale, **item_data)
        
        # Execute external operations
        process_sale_inventory_and_ledger(sale)
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
            
        process_return_inventory_and_ledger(sale_return)
        mock_create_audit_event(sale_return.original_sale)
        
    return sale_return
