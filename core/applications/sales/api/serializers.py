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

    def validate(self, attrs):
        attrs = super().validate(attrs)
        request = self.context.get("request")
        user = getattr(request, "user", None)
        org = attrs.get("organization") or (self.instance.organization if self.instance else None)

        if org and user and user.is_authenticated:
            from core.applications.users.services import has_org_permission
            from core.helper.enums import PermissionCode

            has_discount = False
            discount_amount = attrs.get("discount_amount")
            if discount_amount and discount_amount > 0:
                has_discount = True

            items = attrs.get("items", [])
            for item in items:
                item_discount = item.get("discount")
                if item_discount and item_discount > 0:
                    has_discount = True
                    break

            if has_discount:
                if not has_org_permission(user, org, PermissionCode.APPLY_DISCOUNT):
                    raise serializers.ValidationError(
                        {"discount_amount": "You do not have permission to apply discounts in this organization."}
                    )
        return attrs

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
