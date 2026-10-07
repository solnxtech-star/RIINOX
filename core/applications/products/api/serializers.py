from rest_framework import serializers
from core.applications.products.models import Product, ProductBatch, ProductBulkDiscount, ProductUnitConversion
from core.applications.inventory.services import create_opening_stock
from core.applications.warehouse.models import Warehouse

class ProductBatchSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductBatch
        fields = ['id', 'batch_number', 'expiry_date', 'cost_price', 'selling_price']

class ProductBulkDiscountSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductBulkDiscount
        fields = ['id', 'min_quantity', 'discount_amount']

class ProductUnitConversionSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductUnitConversion
        fields = ['id', 'unit_name', 'multiplier', 'price_override']

class ProductSerializer(serializers.ModelSerializer):
    batches = ProductBatchSerializer(many=True, read_only=True)
    bulk_discounts = ProductBulkDiscountSerializer(many=True, read_only=True)
    unit_conversions = ProductUnitConversionSerializer(many=True, read_only=True)
    current_stock = serializers.IntegerField(read_only=True)

    class Meta:
        model = Product
        fields = '__all__'

class ProductCreateSerializer(serializers.ModelSerializer):
    quantity_in_stock = serializers.IntegerField(write_only=True, required=False, default=0, help_text="Initial opening stock quantity")
    warehouse_id = serializers.PrimaryKeyRelatedField(
        queryset=Warehouse.objects.all(),
        write_only=True, 
        required=False, 
        help_text="Warehouse ID for opening stock"
    )
    
    batches = ProductBatchSerializer(many=True, required=False)
    bulk_discounts = ProductBulkDiscountSerializer(many=True, required=False)
    unit_conversions = ProductUnitConversionSerializer(many=True, required=False)

    class Meta:
        model = Product
        fields = '__all__'
        read_only_fields = ['status']

    def create(self, validated_data):
        quantity_in_stock = validated_data.pop('quantity_in_stock', 0)
        warehouse = validated_data.pop('warehouse_id', None)
        batches_data = validated_data.pop('batches', [])
        discounts_data = validated_data.pop('bulk_discounts', [])
        units_data = validated_data.pop('unit_conversions', [])

        product = super().create(validated_data)

        # Nested creation
        for batch in batches_data:
            ProductBatch.objects.create(product=product, **batch)
        for discount in discounts_data:
            ProductBulkDiscount.objects.create(product=product, **discount)
        for unit in units_data:
            ProductUnitConversion.objects.create(product=product, **unit)

        # Trigger Opening Stock Service
        if quantity_in_stock > 0 and warehouse:
            user = self.context['request'].user
            create_opening_stock(
                product=product,
                variant=None,
                batch=None,
                warehouse=warehouse,
                quantity=quantity_in_stock,
                user=user
            )

        return product
