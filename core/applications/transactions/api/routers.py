from __future__ import annotations

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from core.applications.transactions.api.views import (
    CustomerViewSet,
    TransactionViewSet,
)

router = DefaultRouter()
router.register(
    r"organizations/(?P<organization_id>[0-9a-fA-F-]{32,36})/customers",
    CustomerViewSet,
    basename="customers",
)
router.register(
    r"organizations/(?P<organization_id>[0-9a-fA-F-]{32,36})/transactions",
    TransactionViewSet,
    basename="transactions",
)

app_name = "transactions"

urlpatterns = [
    path("", include(router.urls)),
]
