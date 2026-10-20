from __future__ import annotations

from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated

from core.applications.subscriptions.services.entitlements import (
    ensure_subscription_active,
)
from core.applications.users.permissions import OrganizationScopedMixin
from core.applications.warehouse.api.permissions import (
    StockLocationPermission,
    WarehousePermission,
)
from core.applications.warehouse.api.schemas import (
    stock_location_schema,
    warehouse_schema,
)
from core.applications.warehouse.api.serializers import (
    StockLocationSerializer,
    WarehouseSerializer,
)
from core.applications.warehouse.models import StockLocation, Warehouse
from core.applications.warehouse.services import (
    deactivate_warehouse,
    delete_warehouse,
    reactivate_warehouse,
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

    def perform_update(self, serializer):
        instance = self.get_object()
        new_is_active = serializer.validated_data.get("is_active")
        if new_is_active is not None and new_is_active != instance.is_active:
            if new_is_active:
                reactivate_warehouse(instance)
            else:
                deactivate_warehouse(instance)
        serializer.save()

    def perform_destroy(self, instance):
        delete_warehouse(warehouse=instance, user=self.request.user)


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
        ensure_subscription_active(self.organization)
        serializer.save()

