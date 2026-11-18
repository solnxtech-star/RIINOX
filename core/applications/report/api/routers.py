from __future__ import annotations

from django.conf import settings
from rest_framework.routers import DefaultRouter
from rest_framework.routers import SimpleRouter

from core.applications.report.api.views import ReportViewSet

router = DefaultRouter() if settings.DEBUG else SimpleRouter()
router.register(
    r"organizations/(?P<organization_id>[0-9a-fA-F-]{32,36})/reports",
    ReportViewSet,
    basename="organization-reports",
)

app_name = "report"
urlpatterns = router.urls
