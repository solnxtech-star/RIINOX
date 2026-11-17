from __future__ import annotations

from django.conf import settings
from django.urls import re_path
from rest_framework.routers import DefaultRouter
from rest_framework.routers import SimpleRouter

from core.applications.notification.api.views import MyNotificationPreferencesView
from core.applications.notification.api.views import NotificationSettingsView
from core.applications.notification.api.views import NotificationViewSet

ORG = r"organizations/(?P<organization_id>[0-9a-fA-F-]{32,36})"

router = DefaultRouter() if settings.DEBUG else SimpleRouter()
router.register(rf"{ORG}/notifications", NotificationViewSet, basename="organization-notifications")

app_name = "notifications"
urlpatterns = [
    re_path(rf"^{ORG}/notification-settings/$", NotificationSettingsView.as_view(), name="notification-settings"),
    re_path(
        rf"^{ORG}/notification-preferences/$",
        MyNotificationPreferencesView.as_view(),
        name="notification-preferences",
    ),
    *router.urls,
]
