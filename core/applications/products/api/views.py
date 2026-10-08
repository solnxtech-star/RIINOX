from rest_framework import viewsets, mixins, status
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.db.models import ProtectedError

from core.applications.products.models import Product
from core.applications.products.api.serializers import ProductSerializer, ProductCreateSerializer
from core.applications.products.api.schemas import product_schema
from core.helper.enums import ProductStatusChoices

from core.applications.notification.audit.services import record as audit_record
from core.applications.notification.audit.context import AuditContext
from core.applications.notification.audit.action import AuditAction

@product_schema
class ProductViewSet(viewsets.ModelViewSet):
    queryset = Product.objects.all().prefetch_related('batches', 'bulk_discounts', 'unit_conversions')
    permission_classes = [IsAuthenticated]
    
    def get_serializer_class(self):
        if self.action in ['create', 'update', 'partial_update']:
            return ProductCreateSerializer
        return ProductSerializer

    def perform_create(self, serializer):
        product = serializer.save(created_by=self.request.user)
        audit_record(
            action=AuditAction.PRODUCT_CREATED,
            organization=product.organization,
            actor=self.request.user,
            resource=product,
            context=AuditContext.from_request(self.request),
            metadata={"sku": product.sku}
        )

    def perform_update(self, serializer):
        old_instance = self.get_object()
        old_data = {
            "name": old_instance.name,
            "category_id": old_instance.category_id,
            "status": old_instance.status,
            "customer_sale_price": str(old_instance.customer_sale_price)
        }
        product = serializer.save()
        new_data = {
            "name": product.name,
            "category_id": product.category_id,
            "status": product.status,
            "customer_sale_price": str(product.customer_sale_price)
        }
        audit_record(
            action=AuditAction.PRODUCT_UPDATED,
            organization=product.organization,
            actor=self.request.user,
            resource=product,
            previous_values=old_data,
            new_values=new_data,
            context=AuditContext.from_request(self.request)
        )

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        context = AuditContext.from_request(request)
        try:
            self.perform_destroy(instance)
            # Note: We don't have a PRODUCT_DELETED action currently. 
            # In a real scenario, we might want to log it if soft delete isn't used.
            return Response(status=status.HTTP_204_NO_CONTENT)
        except ProtectedError:
            instance.status = ProductStatusChoices.ARCHIVED
            instance.save(update_fields=['status'])
            
            audit_record(
                action=AuditAction.PRODUCT_ARCHIVED,
                organization=request.user.organization,
                actor=request.user,
                resource=instance,
                context=context,
                reason="Archived due to existing inventory records preventing deletion."
            )
            
            return Response(
                {"detail": f"'{instance.name}' has existing inventory records and was archived instead of deleted."},
                status=status.HTTP_200_OK
            )
