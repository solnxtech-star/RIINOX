from django.conf import settings
from rest_framework.routers import DefaultRouter, SimpleRouter
from core.applications.products.api.views import (
    BrandViewSet,
    CategoryViewSet,
    ProductImageViewSet,
    ProductViewSet,
    ProductVariantViewSet,
)

router = DefaultRouter() if settings.DEBUG else SimpleRouter()

router.register(
    r"organizations/(?P<organization_id>[0-9a-fA-F-]{32,36})/products",
    ProductViewSet,
    basename="organization-products",
)

router.register(
    r"organizations/(?P<organization_id>[0-9a-fA-F-]{32,36})/products/(?P<product_id>[0-9a-fA-F-]{32,36})/variants",
    ProductVariantViewSet,
    basename="organization-product-variants",
)

router.register(
    r"organizations/(?P<organization_id>[0-9a-fA-F-]{32,36})/products/(?P<product_id>[0-9a-fA-F-]{32,36})/images",
    ProductImageViewSet,
    basename="organization-product-images",
)

router.register(
    r"organizations/(?P<organization_id>[0-9a-fA-F-]{32,36})/categories",
    CategoryViewSet,
    basename="organization-categories",
)

router.register(
    r"organizations/(?P<organization_id>[0-9a-fA-F-]{32,36})/brands",
    BrandViewSet,
    basename="organization-brands",
)

app_name = "products"
urlpatterns = router.urls
