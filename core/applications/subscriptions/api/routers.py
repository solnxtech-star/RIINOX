from __future__ import annotations

from django.urls import path
from rest_framework.routers import SimpleRouter

from core.applications.subscriptions.api.views import BillingWebhookView
from core.applications.subscriptions.api.views import OrganizationBillingViewSet
from core.applications.subscriptions.api.views import PlanCatalogueView

router = SimpleRouter()
router.register(
    # Same organization-id pattern as the other apps: with or without dashes.
    r"organizations/(?P<organization_id>[0-9a-fA-F-]{32,36})/billing",
    OrganizationBillingViewSet,
    basename="organization-billing",
)

app_name = "billing"
urlpatterns = [
    path("billing/plans/", PlanCatalogueView.as_view(), name="plans"),
    path("billing/webhooks/<slug:provider>/", BillingWebhookView.as_view(), name="webhook"),
    *router.urls,
]
