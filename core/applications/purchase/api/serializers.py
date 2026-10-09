from rest_framework import serializers
from django.db import transaction
from core.applications.purchase.models import Purchase, PurchaseItem


class PurchaseItemSerializer(serializers.ModelSerializer):
    received_value = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    ordered_value = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = PurchaseItem
        fields = [
            'id', 'purchase', 'product', 'variant', 'unit',
            'quantity_ordered', 'quantity_received', 'purchase_cost',
            'discount', 'received_value', 'ordered_value', 'created_at'
        ]
        read_only_fields = ['id', 'purchase', 'quantity_received', 'created_at']


class PurchaseItemCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = PurchaseItem
        fields = [
            'product', 'variant', 'unit',
            'quantity_ordered', 'purchase_cost', 'discount'
        ]


class PurchaseSerializer(serializers.ModelSerializer):
    items = PurchaseItemSerializer(many=True, read_only=True)
    total_value = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    ordered_value = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)

    class Meta:
        model = Purchase
        fields = [
            'id', 'purchase_number', 'organization', 'vendor', 'warehouse',
            'ordered_by', 'received_by', 'vendor_invoice_number',
            'supporting_document', 'delivery_date', 'order_date',
            'delivery_method', 'other_costs', 'discount', 'status',
            'notes', 'items', 'total_value', 'ordered_value', 'created_at'
        ]
        read_only_fields = ['id', 'purchase_number', 'status', 'received_by', 'created_at']


class PurchaseCreateSerializer(serializers.ModelSerializer):
    items = PurchaseItemCreateSerializer(many=True, required=False, help_text="List of line items ordered from the vendor.")

    class Meta:
        model = Purchase
        fields = [
            'id', 'purchase_number', 'organization', 'vendor', 'warehouse',
            'ordered_by', 'vendor_invoice_number', 'supporting_document',
            'delivery_date', 'order_date', 'delivery_method',
            'other_costs', 'discount', 'status', 'notes', 'items', 'created_at'
        ]
        read_only_fields = ['id', 'purchase_number', 'created_at']

    def create(self, validated_data):
        items_data = validated_data.pop('items', [])
        if not validated_data.get('ordered_by') and 'request' in self.context:
            validated_data['ordered_by'] = self.context['request'].user

        with transaction.atomic():
            purchase = Purchase.objects.create(**validated_data)
            for item_data in items_data:
                PurchaseItem.objects.create(purchase=purchase, **item_data)
        return purchase

    def update(self, instance, validated_data):
        items_data = validated_data.pop('items', None)
        with transaction.atomic():
            instance = super().update(instance, validated_data)
            if items_data is not None:
               update if instance.items.filter(quantity_received__gt=0).exists():
                    raise serializers.ValidationError(
                        "Cannot modify line items on a purchase order that has already received goods."
                    )
                instance.items.all().delete()
                for item_data in items_data:
                    PurchaseItem.objects.create(purchase=instance, **item_data)
        return instance


class PurchaseReceiveItemSerializer(serializers.Serializer):
    item_id = serializers.UUIDField(help_text="UUID of the PurchaseItem being received.")
    quantity_received = serializers.IntegerField(min_value=1, help_text="Number of units physically received.")


class PurchaseReceiveSerializer(serializers.Serializer):
    items_received = PurchaseReceiveItemSerializer(many=True, help_text="Array of items and quantities received.")


class PurchaseReceiveResponseSerializer(serializers.Serializer):
    status = serializers.CharField(help_text="Operation result status (e.g. 'received').")
    purchase_status = serializers.CharField(help_text="Updated lifecycle status of the purchase order.")
    items_received_count = serializers.IntegerField(help_text="Count of distinct line items processed.")
