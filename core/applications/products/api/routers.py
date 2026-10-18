from django.conf import settings
from rest_framework.routers import DefaultRouter, SimpleRouter
from core.applications.products.api.views import ProductViewSet, ProductVariantViewSet

router = DefaultRouter() if settings.DEBUG else SimpleRouter()

# Primary Product REST endpoint scoped by tenant organization
router.register(
    r"organizations/(?P<organization_id>[0-9a-fA-F-]{32,36})/products",
    ProductViewSet,
    basename="organization-products",
)

# Dedicated Product Variant sub-resource endpoint scoped by tenant and parent product (PRD §13/§14)
router.register(
    r"organizations/(?P<organization_id>[0-9a-fA-F-]{32,36})/products/(?P<product_id>[0-9a-fA-F-]{32,36})/variants",
    ProductVariantViewSet,
    basename="organization-product-variants",
)

app_name = "products"
urlpatterns = router.urls

