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
        fields = [
            'id', 'visible', 'created_at', 'updated_at', 'name', 'sku', 'barcode',
            'description', 'quality_grade', 'unit_of_measurement', 'product_type',
            'tax_config', 'tax_rate', 'track_inventory', 'track_batches',
            'purchase_cost', 'vendor_sale_cost', 'customer_sale_price',
            'minimum_stock_level', 'low_stock_alert_unit', 'opening_stock',
            'status', 'organization', 'category', 'brand', 'created_by',
            'current_stock', 'batches', 'bulk_discounts', 'unit_conversions'
        ]

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
        fields = [
            'id', 'visible', 'created_at', 'updated_at', 'name', 'sku', 'barcode',
            'description', 'quality_grade', 'unit_of_measurement', 'product_type',
            'tax_config', 'tax_rate', 'track_inventory', 'track_batches',
            'purchase_cost', 'vendor_sale_cost', 'customer_sale_price',
            'minimum_stock_level', 'low_stock_alert_unit', 'opening_stock',
            'status', 'organization', 'category', 'brand', 'created_by',
            'quantity_in_stock', 'warehouse_id', 'batches', 'bulk_discounts', 'unit_conversions'
        ]
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

    def update(self, instance, validated_data):
        # Discard opening stock fields as they are only relevant on creation
        validated_data.pop('quantity_in_stock', None)
        validated_data.pop('warehouse_id', None)

        # Extract nested data
        batches_data = validated_data.pop('batches', None)
        discounts_data = validated_data.pop('bulk_discounts', None)
        units_data = validated_data.pop('unit_conversions', None)

        # Update the main product instance
        instance = super().update(instance, validated_data)

        # Handle Batches (update existing by batch_number, else create)
        if batches_data is not None:
            for batch_data in batches_data:
                batch_number = batch_data.get('batch_number')
                if batch_number:
                    ProductBatch.objects.update_or_create(
                        product=instance,
                        batch_number=batch_number,
                        defaults=batch_data
                    )
                else:
                    ProductBatch.objects.create(product=instance, **batch_data)

        # Handle Bulk Discounts (full replace)
        if discounts_data is not None:
            instance.bulk_discounts.all().delete()
            for discount_data in discounts_data:
                ProductBulkDiscount.objects.create(product=instance, **discount_data)

        # Handle Unit Conversions (full replace)
        if units_data is not None:
            instance.unit_conversions.all().delete()
            for unit_data in units_data:
                ProductUnitConversion.objects.create(product=instance, **unit_data)

        return instance
