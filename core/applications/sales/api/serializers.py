from rest_framework import serializers
from core.applications.sales.models import Sale, SaleItem, SaleReturn, SaleReturnItem

class SaleItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = SaleItem
        fields = [
            'id', 'sale', 'product', 'variant', 'product_name', 'product_sku', 
            'quantity', 'returned_quantity', 'unit_name', 'unit_multiplier',
            'unit_price', 'discount', 'discount_type', 'tax', 'tax_rate', 'total'
        ]
        read_only_fields = ['total', 'returned_quantity', 'unit_multiplier']

class SaleItemCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = SaleItem
        fields = [
            'product', 'variant', 'product_name', 'product_sku', 
            'quantity', 'unit_name', 'unit_price', 'discount', 'discount_type', 
            'tax', 'tax_rate'
        ]

class SaleSerializer(serializers.ModelSerializer):
    items = SaleItemSerializer(many=True, read_only=True)

    class Meta:
        model = Sale
        fields = [
            'id', 'sale_id', 'organization', 'customer', 'location', 
            'sales_rep', 'payment_status', 'fulfillment_status', 'currency',
            'reference_number', 'due_date', 'billing_address', 'shipping_address',
            'subtotal', 'discount_amount', 'tax_amount', 'total', 'notes', 
            'items', 'created_at'
        ]
        read_only_fields = ['sale_id', 'total', 'created_at']

class SaleCreateSerializer(serializers.ModelSerializer):
    items = SaleItemCreateSerializer(many=True)

    class Meta:
        model = Sale
        fields = [
            'organization', 'customer', 'location', 
            'sales_rep', 'payment_status', 'fulfillment_status', 'currency',
            'reference_number', 'due_date', 'billing_address', 'shipping_address',
            'subtotal', 'discount_amount', 'tax_amount', 'notes', 'items'
        ]

class SaleReturnItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = SaleReturnItem
        fields = ['id', 'sale_return', 'sale_item', 'quantity', 'refund_amount', 'condition', 'restock_action']

class SaleReturnItemCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = SaleReturnItem
        fields = ['sale_item', 'quantity', 'refund_amount', 'condition', 'restock_action']

class SaleReturnSerializer(serializers.ModelSerializer):
    items = SaleReturnItemSerializer(many=True, read_only=True)

    class Meta:
        model = SaleReturn
        fields = ['id', 'original_sale', 'organization', 'return_reason', 'refund_amount', 'refund_method', 'status', 'items']
        read_only_fields = ['status']

class SaleReturnCreateSerializer(serializers.ModelSerializer):
    items = SaleReturnItemCreateSerializer(many=True)

    class Meta:
        model = SaleReturn
        fields = ['original_sale', 'organization', 'return_reason', 'refund_amount', 'refund_method', 'items']
