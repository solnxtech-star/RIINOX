from django.urls import path, include
from rest_framework.routers import DefaultRouter
from core.applications.inventory.api.views import (
    StockAdjustmentViewSet, StockTransferViewSet,
    InventoryViewSet, InventoryLedgerEntryViewSet
)

router = DefaultRouter()
router.register(r'adjustments', StockAdjustmentViewSet, basename='adjustments')
router.register(r'transfers', StockTransferViewSet, basename='transfers')
router.register(r'balances', InventoryViewSet, basename='inventory-balances')
router.register(r'ledger', InventoryLedgerEntryViewSet, basename='inventory-ledger')

app_name = 'inventory'

urlpatterns = [
    path('', include(router.urls)),
]
