"""
The WebSocket side of config.asgi.application (HTTP goes to Django as before).

Order: origin check -> JWT from ?token= -> URL routing -> consumer.
"""

from django.conf import settings
from channels.routing import URLRouter

from core.applications.notification.routing import websocket_urlpatterns
from core.helper.ws_auth import FrontendOriginValidator
from core.helper.ws_auth import JWTQueryAuthMiddleware


def _allowed_origins() -> list[str]:
    if getattr(settings, "CORS_ALLOW_ALL_ORIGINS", False):
        return ["*"]
    origins = list(getattr(settings, "CORS_ALLOWED_ORIGINS", []) or [])
    frontend = (getattr(settings, "FRONTEND_URL", "") or "").rstrip("/")
    if frontend:
        origins.append(frontend)
    return sorted(set(origins))


websocket_application = FrontendOriginValidator(
    JWTQueryAuthMiddleware(URLRouter(websocket_urlpatterns)),
    _allowed_origins(),
)
