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

    def perform_create(self, serializer):
        from core.applications.notification.audit.services import record as audit_record
        from core.applications.notification.audit.context import AuditContext
        from core.applications.notification.audit.action import AuditAction
        
        purchase = serializer.save()
        audit_record(
            action=AuditAction.PURCHASE_ORDER_CREATED,
            organization=purchase.organization,
            actor=self.request.user,
            resource=purchase,
            context=AuditContext.from_request(self.request)
        )

    def perform_update(self, serializer):
        from core.applications.notification.audit.services import record as audit_record
        from core.applications.notification.audit.context import AuditContext
        from core.applications.notification.audit.action import AuditAction

        old_instance = self.get_object()
        old_data = {"status": old_instance.status}
        
        purchase = serializer.save()
        new_data = {"status": purchase.status}
        
        # We can just use PURCHASE_ORDER_APPROVED if status changed to approved,
        # but tracking generic update is safer if we don't have the exact enum value.
        action = AuditAction.PURCHASE_ORDER_APPROVED if old_instance.status != purchase.status and purchase.status == 'approved' else "purchase_order.updated"
        
        audit_record(
            action=action,
            organization=purchase.organization,
            actor=self.request.user,
            resource=purchase,
            previous_values=old_data,
            new_values=new_data,
            context=AuditContext.from_request(self.request)
        )

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
            return Response({"code": "RECEIVE_ERROR", "detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
