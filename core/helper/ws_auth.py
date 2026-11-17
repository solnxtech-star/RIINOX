"""
WebSocket authentication and origin checks.

Browsers can't set an Authorization header on a WebSocket, so the client passes
its normal JWT access token as `?token=<access>`. It is validated exactly like
an API request (signature + expiry), and the connection is closed when the token
expires. Never log query strings in front of this endpoint.
"""

from __future__ import annotations

from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.middleware import BaseMiddleware
from channels.security.websocket import OriginValidator
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.tokens import AccessToken


@database_sync_to_async
def _user_from_token(raw: str | None):
    if not raw:
        return AnonymousUser(), None
    try:
        token = AccessToken(raw)
    except TokenError:
        return AnonymousUser(), None
    user = (
        get_user_model()
        .objects.filter(**{api_settings.USER_ID_FIELD: token.get(api_settings.USER_ID_CLAIM)}, is_active=True)
        .first()
    )
    return (user or AnonymousUser()), token.get("exp")


class JWTQueryAuthMiddleware(BaseMiddleware):
    """Sets scope["user"] (AnonymousUser when the token is missing/invalid) and scope["token_exp"]."""

    async def __call__(self, scope, receive, send):
        query = parse_qs((scope.get("query_string") or b"").decode())
        scope = dict(scope)
        scope["user"], scope["token_exp"] = await _user_from_token((query.get("token") or [None])[0])
        return await super().__call__(scope, receive, send)


class FrontendOriginValidator(OriginValidator):
    """
    Accept browser connections only from our frontends (blocks other sites from
    opening a socket in a victim's browser). Clients that send no Origin header
    (mobile apps, servers) aren't browsers, so they can't be used that way: allowed,
    and still subject to the token check.
    """

    def valid_origin(self, parsed_origin):
        if parsed_origin is None:
            return True
        return super().valid_origin(parsed_origin)
