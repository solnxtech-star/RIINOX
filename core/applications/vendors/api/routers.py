from __future__ import annotations

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from core.applications.vendors.api.views import VendorViewSet

router = DefaultRouter()
router.register(
    r"organizations/(?P<organization_id>[0-9a-fA-F-]{32,36})/vendors",
    VendorViewSet,
    basename="vendors",
)

app_name = "vendors"

urlpatterns = [
    path("", include(router.urls)),
]
