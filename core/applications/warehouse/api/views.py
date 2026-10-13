from __future__ import annotations

from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated

from core.applications.users.permissions import OrganizationScopedMixin
from core.applications.warehouse.models import Warehouse, StockLocation
from core.applications.warehouse.api.permissions import (
    WarehousePermission,
    StockLocationPermission,
)
from core.applications.warehouse.api.serializers import (
    WarehouseSerializer,
    StockLocationSerializer,
)
from core.applications.warehouse.api.schemas import (
    warehouse_schema,
    stock_location_schema,
)


@warehouse_schema
class WarehouseViewSet(OrganizationScopedMixin, viewsets.ModelViewSet):
    """
    CRUD ViewSet for physical Warehouses scoped to the tenant organization.
    """
    queryset = Warehouse.objects.none()
    permission_classes = [IsAuthenticated, WarehousePermission]
    serializer_class = WarehouseSerializer

    def get_queryset(self):
        user = self.request.user
        org = self.organization
        if not org:
            return Warehouse.objects.none()
        return Warehouse.objects.for_organization(org).for_user(user)

    def perform_create(self, serializer):
        serializer.save(organization=self.organization)


@stock_location_schema
class StockLocationViewSet(OrganizationScopedMixin, viewsets.ModelViewSet):
    """
    CRUD ViewSet for Stock Locations (aisle/rack/bin) within tenant Warehouses.
    """
    queryset = StockLocation.objects.none()
    permission_classes = [IsAuthenticated, StockLocationPermission]
    serializer_class = StockLocationSerializer

    def get_queryset(self):
        user = self.request.user
        org = self.organization
        if not org:
            return StockLocation.objects.none()
        return StockLocation.objects.for_organization(org).for_user(user)

    def perform_create(self, serializer):
        serializer.save()
