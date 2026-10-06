from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from core.applications.purchase.models import Purchase
from core.applications.purchase.api.serializers import PurchaseSerializer, PurchaseReceiveSerializer
from core.applications.purchase.api.schemas import purchase_schema
from core.applications.inventory.services import receive_purchase_order

@purchase_schema
class PurchaseViewSet(viewsets.ModelViewSet):
    queryset = Purchase.objects.all().prefetch_related('items')
    serializer_class = PurchaseSerializer
    permission_classes = [IsAuthenticated]

    @action(detail=True, methods=['post'])
    def receive(self, request, pk=None):
        purchase = self.get_object()
        serializer = PurchaseReceiveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        try:
            receive_purchase_order(
                purchase=purchase,
                items_received=serializer.validated_data['items_received'],
                user=request.user
            )
            return Response({"status": "received"}, status=status.HTTP_200_OK)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
