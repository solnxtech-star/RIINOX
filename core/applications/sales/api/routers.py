from django.conf import settings
from rest_framework.routers import DefaultRouter, SimpleRouter

from core.applications.sales.api.views import SaleViewSet, SaleReturnViewSet

router = DefaultRouter() if settings.DEBUG else SimpleRouter()
router.register(r"sales", SaleViewSet, basename="sales")
router.register(r"returns", SaleReturnViewSet, basename="returns")

app_name = "sales"
urlpatterns = router.urls
