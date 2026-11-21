"""
Ending JWT sessions (simplejwt token blacklist).

A "session" is a refresh token. Revoking it means it can't be used to get new
access tokens; the access token already issued keeps working until it expires,
which is why access tokens are short-lived (SIMPLE_JWT ACCESS_TOKEN_LIFETIME).
"""

from __future__ import annotations

from typing import Any

from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken
from rest_framework_simplejwt.token_blacklist.models import OutstandingToken
from rest_framework_simplejwt.tokens import RefreshToken

from core.helper.custom_exceptions import CustomError

__all__ = ["revoke_all_sessions", "revoke_refresh_token"]


def revoke_refresh_token(user: Any, raw_refresh: str) -> None:
    """Revoke one refresh token, which must belong to `user`. Revoking it twice is fine."""
    try:
        token = RefreshToken(raw_refresh)
    except TokenError as exc:
        if "blacklisted" in str(exc).lower():
            return  # already logged out
        raise CustomError.BadRequest({"refresh": "This refresh token is invalid or expired."}) from exc
    if str(token.get(api_settings.USER_ID_CLAIM)) != str(user.pk):
        raise CustomError.BadRequest({"refresh": "This refresh token belongs to another account."})
    token.blacklist()


def revoke_all_sessions(user: Any) -> int:
    """
    Revoke every refresh token issued to `user` (log out everywhere). Used on logout-all
    and whenever the password changes. Returns how many were revoked now.
    """
    outstanding = OutstandingToken.objects.filter(user=user).exclude(blacklistedtoken__isnull=False)
    revoked = 0
    for token in outstanding:
        _, created = BlacklistedToken.objects.get_or_create(token=token)
        revoked += int(created)
    return revoked
