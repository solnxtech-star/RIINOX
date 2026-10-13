from django.conf import settings
from rest_framework.routers import DefaultRouter, SimpleRouter
from core.applications.products.api.views import ProductViewSet

router = DefaultRouter() if settings.DEBUG else SimpleRouter()
router.register(
    r"organizations/(?P<organization_id>[0-9a-fA-F-]{32,36})/products",
    ProductViewSet,
    basename="organization-products",
)

app_name = "products"
urlpatterns = router.urls
