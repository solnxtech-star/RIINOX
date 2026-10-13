from rest_framework import viewsets, status
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from core.applications.sales.models import Sale, SaleReturn
from core.applications.sales.api.permissions import SalePermission, SaleReturnPermission
from core.applications.users.permissions import OrganizationScopedMixin
from core.applications.sales.api.serializers import (
    SaleSerializer,
    SaleCreateSerializer,
    SaleReturnSerializer,
    SaleReturnCreateSerializer,
)
from core.applications.sales.api.schemas import sale_viewset_schema, sale_return_viewset_schema
from core.applications.sales.service import create_sale, create_sale_return


@sale_viewset_schema
class SaleViewSet(OrganizationScopedMixin, viewsets.ModelViewSet):
    queryset = Sale.objects.none()
    permission_classes = [IsAuthenticated, SalePermission]

    def get_queryset(self):
        user = self.request.user
        if not user.is_authenticated:
            return Sale.objects.none()

        if not self.organization:
            return Sale.objects.none()

        return Sale.objects.for_organization(self.organization).with_details()

    def get_serializer_class(self):
        if self.action in ['create', 'update', 'partial_update']:
            return SaleCreateSerializer
        return SaleSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer_class()(data=request.data, context={'request': request, 'view': self})
        serializer.is_valid(raise_exception=True)
        sale = create_sale(serializer.validated_data)
        response_serializer = SaleSerializer(sale, context={'request': request, 'view': self})
        return Response(response_serializer.data, status=status.HTTP_201_CREATED)


@sale_return_viewset_schema
class SaleReturnViewSet(OrganizationScopedMixin, viewsets.ModelViewSet):
    queryset = SaleReturn.objects.none()
    permission_classes = [IsAuthenticated, SaleReturnPermission]

    def get_queryset(self):
        user = self.request.user
        if not user.is_authenticated:
            return SaleReturn.objects.none()

        if not self.organization:
            return SaleReturn.objects.none()

        return SaleReturn.objects.for_organization(self.organization).with_details()

    def get_serializer_class(self):
        if self.action in ['create', 'update', 'partial_update']:
            return SaleReturnCreateSerializer
        return SaleReturnSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer_class()(data=request.data, context={'request': request, 'view': self})
        serializer.is_valid(raise_exception=True)
        sale_return = create_sale_return(serializer.validated_data)
        response_serializer = SaleReturnSerializer(sale_return, context={'request': request, 'view': self})
        return Response(response_serializer.data, status=status.HTTP_201_CREATED)
