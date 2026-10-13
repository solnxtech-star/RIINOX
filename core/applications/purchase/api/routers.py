from django.urls import path, include
from rest_framework.routers import DefaultRouter
from core.applications.purchase.api.views import PurchaseViewSet

router = DefaultRouter()
router.register(
    r"organizations/(?P<organization_id>[0-9a-fA-F-]{32,36})/purchases",
    PurchaseViewSet,
    basename="purchases",
)

app_name = 'purchase'

urlpatterns = [
    path('', include(router.urls)),
]
