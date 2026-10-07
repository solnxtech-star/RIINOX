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
