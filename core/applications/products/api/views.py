from rest_framework import viewsets, mixins, status
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.db.models import ProtectedError

from core.applications.products.models import Product
from core.applications.products.api.serializers import ProductSerializer, ProductCreateSerializer
from core.applications.products.api.schemas import product_schema
from core.helper.enums import ProductStatusChoices

@product_schema
class ProductViewSet(viewsets.ModelViewSet):
    queryset = Product.objects.all().prefetch_related('batches', 'bulk_discounts', 'unit_conversions')
    permission_classes = [IsAuthenticated]
    
    def get_serializer_class(self):
        if self.action in ['create', 'update', 'partial_update']:
            return ProductCreateSerializer
        return ProductSerializer

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        try:
            self.perform_destroy(instance)
            return Response(status=status.HTTP_204_NO_CONTENT)
        except ProtectedError:
            instance.status = ProductStatusChoices.ARCHIVED
            instance.save(update_fields=['status'])
            return Response(
                {"detail": f"'{instance.name}' has existing inventory records and was archived instead of deleted."},
                status=status.HTTP_200_OK
            )
