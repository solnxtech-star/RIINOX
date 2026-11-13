from __future__ import annotations

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from core.applications.warehouse.api.views import (
    LegacyStockLocationViewSet,
    StockLocationViewSet,
    WarehouseViewSet,
)

router = DefaultRouter()
router.register(
    r"organizations/(?P<organization_id>[0-9a-fA-F-]{32,36})/warehouses",
    WarehouseViewSet,
    basename="warehouses",
)
# Storage areas (shelves, racks, bins) inside a location (PRD §35).
router.register(
    r"organizations/(?P<organization_id>[0-9a-fA-F-]{32,36})/storage-areas",
    StockLocationViewSet,
    basename="storage-areas",
)
# Deprecated alias kept so existing clients don't break: "locations" means
# branches everywhere else in the API. Remove once the frontend uses /storage-areas/.
router.register(
    r"organizations/(?P<organization_id>[0-9a-fA-F-]{32,36})/locations",
    LegacyStockLocationViewSet,
    basename="locations",
)

app_name = "warehouse"

urlpatterns = [
    path("", include(router.urls)),
]
