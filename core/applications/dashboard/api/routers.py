from __future__ import annotations

from django.conf import settings
from rest_framework.routers import DefaultRouter
from rest_framework.routers import SimpleRouter

from core.applications.dashboard.api.views import DashboardViewSet

router = DefaultRouter() if settings.DEBUG else SimpleRouter()
router.register(
    r"organizations/(?P<organization_id>[0-9a-fA-F-]{32,36})/dashboard",
    DashboardViewSet,
    basename="organization-dashboard",
)

app_name = "dashboard"
urlpatterns = router.urls
