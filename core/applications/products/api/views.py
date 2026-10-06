from rest_framework import viewsets, mixins
from core.applications.products.models import Product
from core.applications.products.api.serializers import ProductSerializer, ProductCreateSerializer
from core.applications.products.api.schemas import product_schema

from rest_framework.permissions import IsAuthenticated

@product_schema
class ProductViewSet(viewsets.ModelViewSet):
    queryset = Product.objects.all().prefetch_related('batches', 'bulk_discounts', 'unit_conversions')
    permission_classes = [IsAuthenticated]
    
    def get_serializer_class(self):
        if self.action in ['create', 'update', 'partial_update']:
            return ProductCreateSerializer
        return ProductSerializer
