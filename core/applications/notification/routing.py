from django.urls import re_path

from core.applications.notification.consumers import NotificationConsumer

websocket_urlpatterns = [
    re_path(
        r"^ws/organizations/(?P<organization_id>[0-9a-fA-F-]{32,36})/notifications/?$",
        NotificationConsumer.as_asgi(),
    ),
]
