from django.conf import settings
from rest_framework.routers import DefaultRouter
from rest_framework.routers import SimpleRouter

from core.applications.invoice.api.views.invoice_views import InvoiceViewSet
from core.helper.patterns import UUID_RE

PREFIX = "invoices"

API_VERSION = settings.API_VERSION

router = DefaultRouter() if settings.DEBUG else SimpleRouter()

router.register(
    rf"organizations/(?P<organization_id>{UUID_RE})/invoices",
    InvoiceViewSet,
    basename="organization-invoices",
)

app_name =  f"{PREFIX}"
urlpatterns = router.urls
