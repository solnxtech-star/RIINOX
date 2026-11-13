from rest_framework import serializers
from core.applications.inventory.models import StockAdjustmentRequest, Inventory, InventoryLedgerEntry

class StockAdjustmentRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = StockAdjustmentRequest
        fields = '__all__'
        read_only_fields = ['status', 'requested_by', 'reviewed_by', 'reviewed_at', 'rejection_reason']

class StockTransferSerializer(serializers.Serializer):
    product_id = serializers.UUIDField()
    variant_id = serializers.UUIDField(required=False, allow_null=True)
    batch_id = serializers.UUIDField(required=False, allow_null=True)
    source_warehouse_id = serializers.UUIDField()
    destination_warehouse_id = serializers.UUIDField()
    quantity = serializers.IntegerField(min_value=1)

class InventorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Inventory
        fields = '__all__'

class InventoryLedgerEntrySerializer(serializers.ModelSerializer):
    class Meta:
        model = InventoryLedgerEntry
        fields = '__all__'


class InventorySummarySerializer(serializers.Serializer):
    stock_value = serializers.DecimalField(
        max_digits=16,
        decimal_places=2,
        allow_null=True,
        help_text="Total inventory valuation at purchase cost (null if user lacks VIEW_PRODUCT_COSTS).",
    )
    potential_sales_value = serializers.DecimalField(
        max_digits=16,
        decimal_places=2,
        help_text="Total inventory valuation at retail selling price.",
    )
    profit_to_be_made = serializers.DecimalField(
        max_digits=16,
        decimal_places=2,
        allow_null=True,
        help_text="Projected gross profit (potential_sales_value - stock_value, null if user lacks VIEW_PRODUCT_COSTS).",
    )
    stock_requiring_attention = serializers.IntegerField(
        help_text="Total count of active tracked products that are low stock or out of stock.",
    )
    out_of_stock_count = serializers.IntegerField(
        help_text="Count of active tracked products with 0 on-hand stock.",
    )
    low_stock_count = serializers.IntegerField(
        help_text="Count of active tracked products with stock > 0 and <= minimum_stock_level.",
    )
    total_products = serializers.IntegerField(
        help_text="Total count of active products in the organization.",
    )
    total_inventory_items = serializers.IntegerField(
        help_text="Total on-hand unit quantity across all inventory items in scope.",
    )
    warehouses_count = serializers.IntegerField(
        help_text="Total count of active warehouses in the organization.",
    )
    warehouse_id = serializers.UUIDField(
        allow_null=True,
        help_text="UUID of the filtered warehouse, or null when aggregated organization-wide.",
    )
    warehouse_name = serializers.CharField(
        allow_null=True,
        help_text="Name of the filtered warehouse, or 'All Warehouses' when organization-wide.",
    )
    has_cost_permission = serializers.BooleanField(
        help_text="True if the requesting user holds VIEW_PRODUCT_COSTS permission.",
    )
