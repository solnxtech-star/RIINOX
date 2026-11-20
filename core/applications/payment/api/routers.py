from __future__ import annotations

from django.conf import settings
from rest_framework.routers import DefaultRouter
from rest_framework.routers import SimpleRouter

from core.applications.payment.api.views import PaymentViewSet
from core.helper.patterns import UUID_RE

router = DefaultRouter() if settings.DEBUG else SimpleRouter()
router.register(
    rf"organizations/(?P<organization_id>{UUID_RE})/payments",
    PaymentViewSet,
    basename="organization-payments",
)

app_name = "payments"
urlpatterns = router.urls
