from unittest.mock import patch, MagicMock
from django.test import TestCase
from django.core.exceptions import ValidationError
from rest_framework.exceptions import ValidationError as DRFValidationError
from core.applications.inventory.models import Inventory, InventoryLedgerEntry, StockAdjustmentRequest
from core.applications.inventory.services import (
    process_sale_inventory_and_ledger,
    process_return_inventory_and_ledger,
    create_opening_stock,
    request_stock_adjustment,
    approve_stock_adjustment,
    single_transfer_stock,
    bulk_transfer_stock,
    receive_purchase_order,
)
from core.helper.enums import ApprovalStatusChoices, RestockActionChoices
from core.applications.transactions.models import Customer
from core.applications.sales.models import Sale, SaleItem, SaleReturn, SaleReturnItem
from core.applications.purchase.models import Purchase, PurchaseItem
from core.applications.vendors.models import Vendor
from core.applications.inventory.tests.factories import (
    OrganizationFactory, UserFactory, WarehouseFactory,
    StockLocationFactory, ProductFactory, ProductBatchFactory, InventoryFactory
)
import uuid

class InventoryServicesTest(TestCase):
    def setUp(self):
        self.org = OrganizationFactory()
        self.user = UserFactory(email="test@admin.com")
        self.user.is_superuser = True
        self.user.save()
        self.warehouse = WarehouseFactory(organization=self.org)
        self.warehouse2 = WarehouseFactory(organization=self.org)
        self.location = StockLocationFactory(warehouse=self.warehouse)
        self.product = ProductFactory(organization=self.org, track_inventory=True)
        self.customer = Customer.objects.create(organization=self.org, name="Test Cust")

    def test_create_opening_stock(self):
        create_opening_stock(self.product, None, None, self.warehouse, 100, self.user)
        
        self.assertTrue(Inventory.objects.filter(product=self.product, warehouse=self.warehouse, quantity=100).exists())
        self.assertTrue(InventoryLedgerEntry.objects.filter(product=self.product, quantity_moved=100).exists())

    def test_process_sale_inventory_and_ledger(self):
        create_opening_stock(self.product, None, None, self.warehouse, 50, self.user)
        
        sale = Sale.objects.create(
            sale_id=f"SALE-{uuid.uuid4().hex[:5]}",
            organization=self.org,
            customer=self.customer,
            location=self.location,
            sales_rep=self.user
        )
        SaleItem.objects.create(
            sale=sale,
            product=self.product,
            product_name=self.product.name,
            quantity=10,
            unit_multiplier=1.0,
            unit_price=10.0
        )
        
        process_sale_inventory_and_ledger(sale)
        
        inventory = Inventory.objects.get(product=self.product, warehouse=self.warehouse)
        self.assertEqual(inventory.quantity, 40)
        self.assertTrue(InventoryLedgerEntry.objects.filter(product=self.product, quantity_moved=-10).exists())

    def test_process_sale_insufficient_stock(self):
        create_opening_stock(self.product, None, None, self.warehouse, 5, self.user)
        
        sale = Sale.objects.create(
            sale_id=f"SALE-{uuid.uuid4().hex[:5]}",
            organization=self.org,
            customer=self.customer,
            location=self.location,
            sales_rep=self.user
        )
        SaleItem.objects.create(
            sale=sale,
            product=self.product,
            product_name=self.product.name,
            quantity=10,
            unit_multiplier=1.0,
            unit_price=10.0
        )
        
        with self.assertRaises(DRFValidationError):
            process_sale_inventory_and_ledger(sale)

    def test_process_return_inventory_and_ledger(self):
        create_opening_stock(self.product, None, None, self.warehouse, 40, self.user)
        
        sale = Sale.objects.create(
            sale_id=f"SALE-{uuid.uuid4().hex[:5]}",
            organization=self.org,
            customer=self.customer,
            location=self.location,
            sales_rep=self.user
        )
        sale_item = SaleItem.objects.create(
            sale=sale,
            product=self.product,
            product_name=self.product.name,
            quantity=10,
            unit_multiplier=1.0,
            unit_price=10.0
        )
        
        sale_return = SaleReturn.objects.create(
            original_sale=sale,
            organization=self.org,
            return_reason="Defective",
            refund_amount=10.0
        )
        SaleReturnItem.objects.create(
            sale_return=sale_return,
            sale_item=sale_item,
            quantity=5,
            refund_amount=5.0,
            restock_action=RestockActionChoices.RETURN_TO_STOCK
        )
        
        process_return_inventory_and_ledger(sale_return)
        
        inventory = Inventory.objects.get(product=self.product, warehouse=self.warehouse)
        self.assertEqual(inventory.quantity, 45)  # 40 + 5 returned

    def test_request_and_approve_stock_adjustment(self):
        # request
        adj = request_stock_adjustment(
            product=self.product,
            variant=None,
            batch=None,
            warehouse=self.warehouse,
            quantity_change=20,
            adjustment_reason="FOUND",
            notes="Found some",
            requested_unit="pieces",
            user=self.user
        )
        # Should auto-approve because user is superuser
        self.assertEqual(adj.status, ApprovalStatusChoices.APPROVED)
        
        inventory = Inventory.objects.get(product=self.product, warehouse=self.warehouse)
        self.assertEqual(inventory.quantity, 20)

    def test_single_transfer_stock(self):
        create_opening_stock(self.product, None, None, self.warehouse, 50, self.user)
        
        single_transfer_stock(
            source_warehouse=self.warehouse,
            destination_warehouse=self.warehouse2,
            product=self.product,
            variant=None,
            batch=None,
            quantity=20,
            user=self.user
        )
        
        inv1 = Inventory.objects.get(product=self.product, warehouse=self.warehouse)
        inv2 = Inventory.objects.get(product=self.product, warehouse=self.warehouse2)
        
        self.assertEqual(inv1.quantity, 30)
        self.assertEqual(inv2.quantity, 20)

    def test_bulk_transfer_stock(self):
        create_opening_stock(self.product, None, None, self.warehouse, 50, self.user)
        
        items = [{'product': self.product, 'variant': None, 'batch': None, 'quantity': 15}]
        
        bulk_transfer_stock(
            source_warehouse=self.warehouse,
            destination_warehouse=self.warehouse2,
            items=items,
            user=self.user
        )
        
        inv1 = Inventory.objects.get(product=self.product, warehouse=self.warehouse)
        inv2 = Inventory.objects.get(product=self.product, warehouse=self.warehouse2)
        
        self.assertEqual(inv1.quantity, 35)
        self.assertEqual(inv2.quantity, 15)

    def test_receive_purchase_order(self):
        purchase = Purchase.objects.create(
            organization=self.org,
            warehouse=self.warehouse,
            vendor=Vendor.objects.create(organization=self.org, company_name="Mock Vendor"),
            ordered_by=self.user
        )
        p_item = PurchaseItem.objects.create(
            purchase=purchase,
            product=self.product,
            quantity_ordered=100,
            purchase_cost=5.0
        )
        
        items_received = [{'item_id': p_item.id, 'quantity_received': 50}]
        receive_purchase_order(purchase, items_received, self.user)
        
        p_item.refresh_from_db()
        self.assertEqual(p_item.quantity_received, 50)
        
        inventory = Inventory.objects.get(product=self.product, warehouse=self.warehouse)
        self.assertEqual(inventory.quantity, 50)
