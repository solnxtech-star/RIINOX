from rest_framework import serializers
from core.applications.sales.models import Sale, SaleItem, SaleReturn, SaleReturnItem

class SaleItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = SaleItem
        fields = ['id', 'sale', 'product', 'variant', 'quantity', 'unit_price', 'discount', 'tax', 'total']
        read_only_fields = ['total']

class SaleSerializer(serializers.ModelSerializer):
    items = SaleItemSerializer(many=True, read_only=True)

    class Meta:
        model = Sale
        fields = [
            'id', 'sale_id', 'organization', 'customer', 'location', 
            'sales_rep', 'status', 'subtotal', 'discount_amount', 
            'tax_amount', 'total', 'notes', 'items', 'created_at'
        ]
        read_only_fields = ['sale_id', 'status', 'total', 'created_at']

class SaleCreateSerializer(serializers.ModelSerializer):
    items = SaleItemSerializer(many=True)

    class Meta:
        model = Sale
        fields = [
            'organization', 'customer', 'location', 
            'sales_rep', 'subtotal', 'discount_amount', 
            'tax_amount', 'notes', 'items'
        ]

class SaleReturnItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = SaleReturnItem
        fields = ['id', 'sale_return', 'sale_item', 'quantity', 'refund_amount']

class SaleReturnSerializer(serializers.ModelSerializer):
    items = SaleReturnItemSerializer(many=True, read_only=True)

    class Meta:
        model = SaleReturn
        fields = ['id', 'original_sale', 'organization', 'return_reason', 'refund_amount', 'status', 'items']
        read_only_fields = ['status']

class SaleReturnCreateSerializer(serializers.ModelSerializer):
    items = SaleReturnItemSerializer(many=True)

    class Meta:
        model = SaleReturn
        fields = ['original_sale', 'organization', 'return_reason', 'refund_amount', 'items']
