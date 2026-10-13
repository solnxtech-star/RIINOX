from django.conf import settings
from rest_framework.routers import DefaultRouter, SimpleRouter
from core.applications.inventory.api.views import (
    StockAdjustmentViewSet, StockTransferViewSet,
    InventoryViewSet, InventoryLedgerEntryViewSet
)

router = DefaultRouter() if settings.DEBUG else SimpleRouter()
router.register(
    r"organizations/(?P<organization_id>[0-9a-fA-F-]{32,36})/inventory/adjustments",
    StockAdjustmentViewSet,
    basename="organization-inventory-adjustments",
)
router.register(
    r"organizations/(?P<organization_id>[0-9a-fA-F-]{32,36})/inventory/transfers",
    StockTransferViewSet,
    basename="organization-inventory-transfers",
)
router.register(
    r"organizations/(?P<organization_id>[0-9a-fA-F-]{32,36})/inventory/balances",
    InventoryViewSet,
    basename="organization-inventory-balances",
)
router.register(
    r"organizations/(?P<organization_id>[0-9a-fA-F-]{32,36})/inventory/ledger",
    InventoryLedgerEntryViewSet,
    basename="organization-inventory-ledger",
)

app_name = "inventory"
urlpatterns = router.urls
