from decimal import Decimal
from rest_framework import serializers
from drf_spectacular.utils import extend_schema_field
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
            'product', 'variant', 'quantity', 
            'unit_name', 'unit_price', 'discount', 'discount_type', 'tax_rate'
        ]
        extra_kwargs = {
            'variant': {'required': False, 'allow_null': True},
            'unit_name': {'required': False},
            'unit_price': {'required': False},
            'discount': {'required': False},
            'discount_type': {'required': False},
            'tax_rate': {'required': False},
        }

class SaleSerializer(serializers.ModelSerializer):
    items = SaleItemSerializer(many=True, read_only=True)
    change_due = serializers.SerializerMethodField()

    class Meta:
        model = Sale
        fields = [
            'id', 'sale_id', 'organization', 'customer', 'warehouse', 'location', 
            'sales_rep', 'payment_status', 'fulfillment_status', 'currency',
            'reference_number', 'due_date', 'billing_address', 'shipping_address',
            'subtotal', 'discount_amount', 'tax_amount', 'total', 'change_due', 'notes', 
            'cancelled_at', 'cancelled_by', 'cancel_reason',
            'items', 'created_at'
        ]
        read_only_fields = [
            'sale_id', 'total', 'change_due', 'created_at',
            'cancelled_at', 'cancelled_by', 'cancel_reason'
        ]

    @extend_schema_field(serializers.CharField())
    def get_change_due(self, obj) -> str:
        val = getattr(obj, "change_due", Decimal("0.00"))
        return f"{Decimal(str(val)):.2f}"


class SaleCancelSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, default="")


class SalePaymentTenderSerializer(serializers.Serializer):
    amount = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, allow_null=True
    )
    method = serializers.CharField(max_length=50)
    reference = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")


class SaleCreateSerializer(serializers.ModelSerializer):
    items = SaleItemCreateSerializer(many=True)
    payment = SalePaymentTenderSerializer(required=False, allow_null=True)

    class Meta:
        model = Sale
        fields = [
            'organization', 'customer', 'warehouse', 'location', 
            'sales_rep', 'currency',
            'reference_number', 'due_date', 'billing_address', 'shipping_address',
            'discount_amount', 'tax_amount', 'notes', 'items', 'payment'
        ]
        extra_kwargs = {
            'organization': {'required': False},
            'sales_rep': {'required': False},
            'warehouse': {'required': False, 'allow_null': True},
            'location': {'required': False, 'allow_null': True},
            'currency': {'required': False, 'default': 'NGN'},
        }


    def validate(self, attrs):
        attrs = super().validate(attrs)
        request = self.context.get("request")
        view = self.context.get("view")
        user = getattr(request, "user", None)
        url_org = getattr(view, "organization", None) if view else None

        if url_org and attrs.get("organization") and attrs["organization"].id != url_org.id:
            raise serializers.ValidationError({"organization": "Organization does not match URL path."})

        org = attrs.get("organization") or url_org or (self.instance.organization if self.instance else None)
        if org and not attrs.get("organization"):
            attrs["organization"] = org

        if org:
            customer = attrs.get("customer")
            if customer and customer.organization_id != org.id:
                raise serializers.ValidationError({"customer": "Customer does not belong to this organization."})

            warehouse = attrs.get("warehouse")
            if warehouse and warehouse.organization_id != org.id:
                raise serializers.ValidationError({"warehouse": "Warehouse does not belong to this organization."})

            location = attrs.get("location")
            if location and hasattr(location, "warehouse") and location.warehouse.organization_id != org.id:
                raise serializers.ValidationError({"location": "Location does not belong to this organization."})

            items = attrs.get("items", [])
            for item in items:
                prod = item.get("product")
                if prod and prod.organization_id != org.id:
                    raise serializers.ValidationError({"items": f"Product '{prod.name}' does not belong to this organization."})

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
        fields = ['sale_item', 'quantity', 'condition', 'restock_action']

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
        fields = ['original_sale', 'organization', 'return_reason', 'refund_method', 'items']
        extra_kwargs = {
            'organization': {'required': False},
        }

    def validate(self, attrs):
        attrs = super().validate(attrs)
        view = self.context.get("view")
        url_org = getattr(view, "organization", None) if view else None

        if url_org and attrs.get("organization") and attrs["organization"].id != url_org.id:
            raise serializers.ValidationError({"organization": "Organization does not match URL path."})

        org = attrs.get("organization") or url_org or (self.instance.organization if self.instance else None)
        if org and not attrs.get("organization"):
            attrs["organization"] = org

        original_sale = attrs.get("original_sale")

        if org and original_sale:
            if original_sale.organization_id != org.id:
                raise serializers.ValidationError({"original_sale": "Original sale does not belong to this organization."})

        items = attrs.get("items", [])
        if original_sale:
            valid_sale_item_ids = set(original_sale.items.values_list("id", flat=True))
            for item in items:
                sale_item = item.get("sale_item")
                if sale_item and sale_item.id not in valid_sale_item_ids:
                    raise serializers.ValidationError({"items": "Sale item does not belong to the original sale."})

        return attrs
