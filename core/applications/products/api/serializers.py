import uuid
from rest_framework import serializers

from core.applications.products.models import (
    Brand,
    Category,
    Product,
    ProductBatch,
    ProductBulkDiscount,
    ProductUnitConversion,
    ProductVariant,
)
from core.applications.products.services import (
    ProductRelationsInput,
    create_product,
    update_product,
)
from core.applications.users.services import has_org_permission
from core.applications.warehouse.models import Warehouse
from core.helper.enums import PermissionCode


class ProductBatchSerializer(serializers.ModelSerializer):
    """
    Serializer for product batches.
    Validates non-negative costs/prices and masks cost_price for users without VIEW_PRODUCT_COSTS.
    """
    class Meta:
        model = ProductBatch
        fields = ['id', 'batch_number', 'expiry_date', 'cost_price', 'selling_price']

    def validate(self, attrs):
        attrs = super().validate(attrs)
        cost = attrs.get('cost_price')
        if cost is not None and cost < 0:
            raise serializers.ValidationError({'cost_price': 'Cost price cannot be negative.'})
        selling = attrs.get('selling_price')
        if selling is not None and selling < 0:
            raise serializers.ValidationError({'selling_price': 'Selling price cannot be negative.'})
        return attrs

    def to_representation(self, instance):
        """
        Security: Mask purchasing cost if the requesting user lacks VIEW_PRODUCT_COSTS permission.
        """
        data = super().to_representation(instance)
        request = self.context.get('request')
        user = getattr(request, 'user', None)
        org = None
        if hasattr(instance, 'product') and instance.product:
            org = instance.product.organization
        if org and user and user.is_authenticated:
            if not has_org_permission(user, org, PermissionCode.VIEW_PRODUCT_COSTS):
                data.pop('cost_price', None)
        return data


class ProductVariantSerializer(serializers.ModelSerializer):
    """
    Serializer for product variants (PRD §13/§14).
    Validates non-negative costs/prices and masks purchase_cost for users without VIEW_PRODUCT_COSTS.
    """
    class Meta:
        model = ProductVariant
        fields = ['id', 'name', 'sku', 'barcode', 'purchase_cost', 'customer_sale_price', 'is_active']
        read_only_fields = ['id']

    def validate(self, attrs):
        attrs = super().validate(attrs)
        cost = attrs.get('purchase_cost')
        if cost is not None and cost < 0:
            raise serializers.ValidationError({'purchase_cost': 'Purchase cost cannot be negative.'})
        price = attrs.get('customer_sale_price')
        if price is not None and price < 0:
            raise serializers.ValidationError({'customer_sale_price': 'Customer sale price cannot be negative.'})
        return attrs

    def to_representation(self, instance):
        """
        Security: Mask purchasing cost if the requesting user lacks VIEW_PRODUCT_COSTS permission.
        """
        data = super().to_representation(instance)
        request = self.context.get('request')
        user = getattr(request, 'user', None)
        org = None
        if hasattr(instance, 'product') and instance.product:
            org = instance.product.organization
        if org and user and user.is_authenticated:
            if not has_org_permission(user, org, PermissionCode.VIEW_PRODUCT_COSTS):
                data.pop('purchase_cost', None)
        return data


class ProductBulkDiscountSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductBulkDiscount
        fields = ['id', 'min_quantity', 'discount_amount']

    def validate_discount_amount(self, value):
        if value < 0:
            raise serializers.ValidationError('Discount amount cannot be negative.')
        return value


class ProductUnitConversionSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductUnitConversion
        fields = ['id', 'unit_name', 'multiplier', 'price_override']

    def validate_multiplier(self, value):
        if value <= 0:
            raise serializers.ValidationError('Multiplier must be greater than zero.')
        return value


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ["id", "name", "description", "is_active", "organization", "created_at", "updated_at"]
        read_only_fields = ["id", "organization", "created_at", "updated_at"]

    def validate_name(self, value):
        view = self.context.get("view")
        org = getattr(view, "organization", None)
        if org:
            name_val = value.strip()
            qs = Category.objects.filter(organization=org, name__iexact=name_val)
            if self.instance:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise serializers.ValidationError("A category with this name already exists in your organization.")
        return value.strip()

    def validate(self, attrs):
        attrs = super().validate(attrs)
        view = self.context.get("view")
        url_org = getattr(view, "organization", None) if view else None
        if url_org and hasattr(self, "initial_data") and isinstance(self.initial_data, dict):
            raw_org = self.initial_data.get("organization")
            if raw_org:
                try:
                    if uuid.UUID(str(raw_org)) != uuid.UUID(str(url_org.id)):
                        raise serializers.ValidationError({"organization": "Organization does not match URL path."})
                except (ValueError, AttributeError):
                    raise serializers.ValidationError({"organization": "Invalid organization UUID."})
        return attrs


class BrandSerializer(serializers.ModelSerializer):
    class Meta:
        model = Brand
        fields = ["id", "name", "is_active", "organization", "created_at", "updated_at"]
        read_only_fields = ["id", "organization", "created_at", "updated_at"]

    def validate_name(self, value):
        view = self.context.get("view")
        org = getattr(view, "organization", None)
        if org:
            name_val = value.strip()
            qs = Brand.objects.filter(organization=org, name__iexact=name_val)
            if self.instance:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise serializers.ValidationError("A brand with this name already exists in your organization.")
        return value.strip()

    def validate(self, attrs):
        attrs = super().validate(attrs)
        view = self.context.get("view")
        url_org = getattr(view, "organization", None) if view else None
        if url_org and hasattr(self, "initial_data") and isinstance(self.initial_data, dict):
            raw_org = self.initial_data.get("organization")
            if raw_org:
                try:
                    if uuid.UUID(str(raw_org)) != uuid.UUID(str(url_org.id)):
                        raise serializers.ValidationError({"organization": "Organization does not match URL path."})
                except (ValueError, AttributeError):
                    raise serializers.ValidationError({"organization": "Invalid organization UUID."})
        return attrs


class ProductSerializer(serializers.ModelSerializer):
    batches = ProductBatchSerializer(many=True, read_only=True)
    bulk_discounts = ProductBulkDiscountSerializer(many=True, read_only=True)
    unit_conversions = ProductUnitConversionSerializer(many=True, read_only=True)
    variants = ProductVariantSerializer(many=True, read_only=True)
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
            'current_stock', 'batches', 'bulk_discounts', 'unit_conversions', 'variants'
        ]
        read_only_fields = ['id', 'status', 'organization', 'created_by', 'created_at', 'updated_at']

    def validate(self, attrs):
        attrs = super().validate(attrs)
        view = self.context.get('view')
        url_org = getattr(view, 'organization', None) if view else None
        org = url_org or (self.instance.organization if self.instance else None)
        if org:
            category = attrs.get('category')
            if category and getattr(category, 'organization_id', None) != org.id:
                raise serializers.ValidationError(
                    {'category': 'Selected category belongs to a different organization.'}
                )

            brand = attrs.get('brand')
            if brand and getattr(brand, 'organization_id', None) != org.id:
                raise serializers.ValidationError(
                    {'brand': 'Selected brand belongs to a different organization.'}
                )
        return attrs

    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get('request')
        user = getattr(request, 'user', None)
        if instance.organization and user and user.is_authenticated:
            if not has_org_permission(user, instance.organization, PermissionCode.VIEW_PRODUCT_COSTS):
                data.pop('purchase_cost', None)
                data.pop('vendor_sale_cost', None)
        return data


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
    variants = ProductVariantSerializer(many=True, required=False)

    class Meta:
        model = Product
        fields = [
            'id', 'visible', 'created_at', 'updated_at', 'name', 'sku', 'barcode',
            'description', 'quality_grade', 'unit_of_measurement', 'product_type',
            'tax_config', 'tax_rate', 'track_inventory', 'track_batches',
            'purchase_cost', 'vendor_sale_cost', 'customer_sale_price',
            'minimum_stock_level', 'low_stock_alert_unit', 'opening_stock',
            'status', 'organization', 'category', 'brand', 'created_by',
            'quantity_in_stock', 'warehouse_id', 'batches', 'bulk_discounts', 'unit_conversions', 'variants'
        ]
        read_only_fields = ['id', 'status', 'organization', 'created_by', 'created_at', 'updated_at']

    def validate(self, attrs):
        attrs = super().validate(attrs)
        request = self.context.get('request')
        view = self.context.get('view')
        user = getattr(request, 'user', None)
        url_org = getattr(view, 'organization', None) if view else None

        if url_org and hasattr(self, 'initial_data') and isinstance(self.initial_data, dict):
            raw_org = self.initial_data.get('organization')
            if raw_org:
                try:
                    if uuid.UUID(str(raw_org)) != uuid.UUID(str(url_org.id)):
                        raise serializers.ValidationError({'organization': 'Organization does not match URL path.'})
                except (ValueError, AttributeError):
                    raise serializers.ValidationError({'organization': 'Invalid organization UUID.'})

        org = url_org or (self.instance.organization if self.instance else None)

        if org:
            category = attrs.get('category')
            if category and getattr(category, 'organization_id', None) != org.id:
                raise serializers.ValidationError(
                    {'category': 'Selected category belongs to a different organization.'}
                )

            brand = attrs.get('brand')
            if brand and getattr(brand, 'organization_id', None) != org.id:
                raise serializers.ValidationError(
                    {'brand': 'Selected brand belongs to a different organization.'}
                )

            warehouse = attrs.get('warehouse_id')
            if warehouse and getattr(warehouse, 'organization_id', None) != org.id:
                raise serializers.ValidationError(
                    {'warehouse_id': 'Selected warehouse belongs to a different organization.'}
                )

        # Enforce non-negative price/cost amounts
        for price_field in ('customer_sale_price', 'purchase_cost', 'vendor_sale_cost'):
            val = attrs.get(price_field)
            if val is not None and val < 0:
                raise serializers.ValidationError({price_field: 'Value cannot be negative.'})

        # Enforce warehouse when opening stock is specified (PRD §15)
        if attrs.get('quantity_in_stock', 0) > 0 and not attrs.get('warehouse_id'):
            raise serializers.ValidationError(
                {'warehouse_id': 'Warehouse is required when opening stock is specified.'}
            )

        if org and user and user.is_authenticated:
            cost_fields = {'purchase_cost', 'vendor_sale_cost'}
            if any(field in attrs for field in cost_fields):
                if not has_org_permission(user, org, PermissionCode.EDIT_PRODUCT_COSTS):
                    raise serializers.ValidationError(
                        {'purchase_cost': 'You do not have permission to edit product costs in this organization.'}
                    )

            if 'customer_sale_price' in attrs and self.instance:
                if self.instance.customer_sale_price != attrs['customer_sale_price']:
                    if not has_org_permission(user, org, PermissionCode.EDIT_SELLING_PRICE):
                        raise serializers.ValidationError(
                            {'customer_sale_price': 'You do not have permission to edit selling prices in this organization.'}
                        )

            # Enforce EDIT_PRODUCT_COSTS on nested batches
            batches = attrs.get('batches') or []
            if any(b.get('cost_price') is not None for b in batches):
                if not has_org_permission(user, org, PermissionCode.EDIT_PRODUCT_COSTS):
                    raise serializers.ValidationError(
                        {'batches': 'You do not have permission to set batch costs.'}
                    )

            # Enforce EDIT_PRODUCT_COSTS on nested variants
            variants = attrs.get('variants') or []
            if any(v.get('purchase_cost') is not None for v in variants):
                if not has_org_permission(user, org, PermissionCode.EDIT_PRODUCT_COSTS):
                    raise serializers.ValidationError(
                        {'variants': 'You do not have permission to set variant purchase costs.'}
                    )
        return attrs

    def create(self, validated_data):
        quantity_in_stock = validated_data.pop('quantity_in_stock', 0)
        warehouse = validated_data.pop('warehouse_id', None)
        batches_data = validated_data.pop('batches', [])
        discounts_data = validated_data.pop('bulk_discounts', [])
        units_data = validated_data.pop('unit_conversions', [])
        variants_data = validated_data.pop('variants', [])

        view = self.context.get('view')
        organization = validated_data.pop('organization', None) or (getattr(view, 'organization', None) if view else None)
        created_by = validated_data.pop('created_by', None)
        if created_by is None and 'request' in self.context:
            created_by = self.context['request'].user

        return create_product(
            organization=organization,
            created_by=created_by,
            quantity_in_stock=quantity_in_stock,
            warehouse=warehouse,
            batches=batches_data,
            bulk_discounts=discounts_data,
            unit_conversions=units_data,
            variants=variants_data,
            **validated_data,
        )

    def update(self, instance, validated_data):
        # Discard opening stock fields as they are only relevant on creation
        validated_data.pop('quantity_in_stock', None)
        validated_data.pop('warehouse_id', None)

        batches_data = validated_data.pop('batches', None)
        discounts_data = validated_data.pop('bulk_discounts', None)
        units_data = validated_data.pop('unit_conversions', None)
        variants_data = validated_data.pop('variants', None)

        user = self.context['request'].user if 'request' in self.context else instance.created_by

        return update_product(
            product=instance,
            user=user,
            batches=batches_data,
            bulk_discounts=discounts_data,
            unit_conversions=units_data,
            variants=variants_data,
            **validated_data,
        )
