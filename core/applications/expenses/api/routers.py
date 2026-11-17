from __future__ import annotations

from django.conf import settings
from rest_framework.routers import DefaultRouter
from rest_framework.routers import SimpleRouter

from core.applications.expenses.api.views import ExpenseViewSet

router = DefaultRouter() if settings.DEBUG else SimpleRouter()
router.register(
    r"organizations/(?P<organization_id>[0-9a-fA-F-]{32,36})/expenses",
    ExpenseViewSet,
    basename="organization-expenses",
)

app_name = "expenses"
urlpatterns = router.urls
