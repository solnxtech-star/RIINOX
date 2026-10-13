from __future__ import annotations

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from core.applications.warehouse.api.views import (
    WarehouseViewSet,
    StockLocationViewSet,
)

router = DefaultRouter()
router.register(
    r"organizations/(?P<organization_id>[0-9a-fA-F-]{32,36})/warehouses",
    WarehouseViewSet,
    basename="warehouses",
)
router.register(
    r"organizations/(?P<organization_id>[0-9a-fA-F-]{32,36})/locations",
    StockLocationViewSet,
    basename="locations",
)

app_name = "warehouse"

urlpatterns = [
    path("", include(router.urls)),
]
