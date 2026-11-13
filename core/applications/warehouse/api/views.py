from __future__ import annotations

from drf_spectacular.utils import extend_schema
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.applications.notification.audit.context import AuditContext
from core.applications.subscriptions.services.entitlements import (
    ensure_subscription_active,
)
from core.applications.users.permissions import OrganizationScopedMixin
from core.applications.warehouse.api.permissions import (
    StockLocationPermission,
    WarehousePermission,
)
from core.applications.warehouse.api.schemas import (
    ORGANIZATION_PATH_PARAM,
    SET_DEFAULT_DESCRIPTION,
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
    set_default_location,
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
        queryset = Warehouse.objects.for_organization(org).for_user(user).order_by("-is_default", "name")
        # Only the caller's locations (PRD §35). No ?location= here: this *is* the location list.
        return self.location_scope.apply(queryset, field="pk")

    def perform_create(self, serializer):
        # A new location is organization-wide; a member limited to some locations
        # couldn't even see it afterwards.
        self.location_scope.require_all_locations()
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

    @extend_schema(
        tags=["Warehouses"],
        summary="Make this the default location",
        description=SET_DEFAULT_DESCRIPTION,
        parameters=[ORGANIZATION_PATH_PARAM],
        request=None,
        responses=WarehouseSerializer,
    )
    @action(detail=True, methods=["post"], url_path="set-default")
    def set_default(self, request, *args, **kwargs):
        location = self.get_object()
        self.location_scope.require_all_locations()
        location = set_default_location(
            location=location,
            actor=request.user,
            audit_context=AuditContext.from_request(request),
        )
        return Response(self.get_serializer(location).data)


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
        queryset = StockLocation.objects.for_organization(org).for_user(user).select_related("warehouse")
        return self.scope_to_locations(queryset, field="warehouse")

    def perform_create(self, serializer):
        ensure_subscription_active(self.organization)
        self.location_scope.require(serializer.validated_data["warehouse"])
        serializer.save()

    def perform_update(self, serializer):
        # Moving a storage area to another location needs access to that location too.
        if "warehouse" in serializer.validated_data:
            self.location_scope.require(serializer.validated_data["warehouse"])
        serializer.save()
