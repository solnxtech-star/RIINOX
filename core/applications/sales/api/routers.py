from django.conf import settings
from rest_framework.routers import DefaultRouter, SimpleRouter

from core.applications.sales.api.views import SaleViewSet, SaleReturnViewSet

router = DefaultRouter() if settings.DEBUG else SimpleRouter()
router.register(
    r"organizations/(?P<organization_id>[0-9a-fA-F-]{32,36})/sales/returns",
    SaleReturnViewSet,
    basename="organization-sales-returns",
)
router.register(
    r"organizations/(?P<organization_id>[0-9a-fA-F-]{32,36})/sales",
    SaleViewSet,
    basename="organization-sales",
)

app_name = "sales"
urlpatterns = router.urls
