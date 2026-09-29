from rest_framework import viewsets, status
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from core.applications.sales.models import Sale, SaleReturn
from core.applications.sales.api.serializers import (
    SaleSerializer,
    SaleCreateSerializer,
    SaleReturnSerializer,
    SaleReturnCreateSerializer,
)
from core.applications.sales.api.schemas import sale_viewset_schema, sale_return_viewset_schema
from core.applications.sales.service import create_sale, create_sale_return

@sale_viewset_schema
class SaleViewSet(viewsets.ModelViewSet):
    queryset = Sale.objects.select_related('organization', 'customer', 'location', 'sales_rep').prefetch_related('items__product', 'items__variant')
    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        if self.action in ['create', 'update', 'partial_update']:
            return SaleCreateSerializer
        return SaleSerializer

    def perform_create(self, serializer):
        # We delegate the actual creation and transaction handling to the service layer.
        # But `perform_create` normally saves the serializer. We override `create` instead to return the proper response.
        pass

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer_class()(data=request.data)
        serializer.is_valid(raise_exception=True)
        sale = create_sale(serializer.validated_data)
        response_serializer = SaleSerializer(sale)
        return Response(response_serializer.data, status=status.HTTP_201_CREATED)

@sale_return_viewset_schema
class SaleReturnViewSet(viewsets.ModelViewSet):
    queryset = SaleReturn.objects.select_related('original_sale', 'organization').prefetch_related('items__sale_item')
    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        if self.action in ['create', 'update', 'partial_update']:
            return SaleReturnCreateSerializer
        return SaleReturnSerializer

    def perform_create(self, serializer):
        pass

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer_class()(data=request.data)
        serializer.is_valid(raise_exception=True)
        sale_return = create_sale_return(serializer.validated_data)
        response_serializer = SaleReturnSerializer(sale_return)
        return Response(response_serializer.data, status=status.HTTP_201_CREATED)
