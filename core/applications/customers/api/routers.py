from __future__ import annotations

from django.conf import settings
from rest_framework.routers import DefaultRouter, SimpleRouter

from core.applications.customers.api.views import CustomerViewSet

router = DefaultRouter() if settings.DEBUG else SimpleRouter()

router.register(
    r"organizations/(?P<organization_id>[0-9a-fA-F-]{32,36})/customers",
    CustomerViewSet,
    basename="organization-customers",
)

app_name = "customers"
urlpatterns = router.urls
