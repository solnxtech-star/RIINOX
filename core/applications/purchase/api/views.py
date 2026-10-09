from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from core.applications.purchase.models import Purchase
from core.applications.purchase.api.serializers import (
    PurchaseSerializer,
    PurchaseCreateSerializer,
    PurchaseReceiveSerializer,
    PurchaseReceiveResponseSerializer,
)
from core.applications.purchase.api.schemas import purchase_schema
from core.applications.inventory.services import receive_purchase_order
from core.applications.notification.audit.services import record as audit_record
from core.applications.notification.audit.context import AuditContext
from core.applications.notification.audit.action import AuditAction
from core.helper.enums import PurchaseStatusChoices


@purchase_schema
class PurchaseViewSet(viewsets.ModelViewSet):
    queryset = Purchase.objects.all().prefetch_related('items__product')
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        qs = Purchase.objects.all().prefetch_related('items__product')
        org_id = self.request.query_params.get('organization')
        if org_id:
            return qs.filter(organization_id=org_id)
        if hasattr(user, 'memberships'):
            org_ids = user.memberships.values_list('organization_id', flat=True)
            if org_ids:
                return qs.filter(organization_id__in=org_ids)
        return qs

    def get_serializer_class(self):
        if self.action in ['create', 'update', 'partial_update']:
            return PurchaseCreateSerializer
        return PurchaseSerializer

    def perform_create(self, serializer):
        purchase = serializer.save()
        audit_record(
            action=AuditAction.PURCHASE_ORDER_CREATED,
            organization=purchase.organization,
            actor=self.request.user,
            resource=purchase,
            context=AuditContext.from_request(self.request),
            metadata={"purchase_number": purchase.purchase_number, "items_count": purchase.items.count()}
        )

    def perform_update(self, serializer):
        old_instance = self.get_object()
        old_data = {"status": old_instance.status}

        purchase = serializer.save()
        new_data = {"status": purchase.status}

        action = (
            AuditAction.PURCHASE_ORDER_APPROVED
            if old_instance.status != purchase.status and purchase.status == PurchaseStatusChoices.ORDERED
            else AuditAction.PURCHASE_ORDER_UPDATED
        )

        audit_record(
            action=action,
            organization=purchase.organization,
            actor=self.request.user,
            resource=purchase,
            previous_values=old_data,
            new_values=new_data,
            context=AuditContext.from_request(self.request)
        )

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        # PRD §18: Orders that have already received goods cannot be deleted
        if instance.items.filter(quantity_received__gt=0).exists():
            return Response(
                {
                    "code": "CANNOT_DELETE_RECEIVED_PURCHASE",
                    "detail": "Cannot delete a purchase order that has already received goods. Cancel it instead."
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        context = AuditContext.from_request(request)
        audit_record(
            action="purchase_order.deleted",
            organization=instance.organization,
            actor=request.user,
            resource=instance,
            context=context,
            reason="Purchase order deleted while still in unreceived state."
        )
        return super().destroy(request, *args, **kwargs)

    @action(detail=True, methods=['post'])
    def receive(self, request, pk=None):
        purchase = self.get_object()
        serializer = PurchaseReceiveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            updated_purchase = receive_purchase_order(
                purchase=purchase,
                items_received=serializer.validated_data['items_received'],
                user=request.user
            )
            data = {
                "status": "received",
                "purchase_status": updated_purchase.status,
                "items_received_count": len(serializer.validated_data['items_received']),
            }
            return Response(data, status=status.HTTP_200_OK)
        except Exception as e:
            return Response({"code": "RECEIVE_ERROR", "detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
