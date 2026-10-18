"""
Last Modified: 2026-10-18
Module: core.applications.products.api.routers
Description:
    URL router registration for the Products API.
    Configures tenant-scoped URL endpoints for Products and nested Product Variants
    using UUID-regex patterns matching both 32-char hex and standard 36-char hyphenated UUIDs.
"""

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

