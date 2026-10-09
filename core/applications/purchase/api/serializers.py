from rest_framework import serializers
from core.applications.purchase.models import Purchase, PurchaseItem

class PurchaseItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = PurchaseItem
        fields = '__all__'

class PurchaseSerializer(serializers.ModelSerializer):
    items = PurchaseItemSerializer(many=True, read_only=True)
    
    class Meta:
        model = Purchase
        fields = '__all__'

class PurchaseReceiveItemSerializer(serializers.Serializer):
    item_id = serializers.UUIDField()
    quantity_received = serializers.IntegerField(min_value=1)

class PurchaseReceiveSerializer(serializers.Serializer):
    items_received = PurchaseReceiveItemSerializer(many=True)
