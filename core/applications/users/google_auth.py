"""
Sign in / sign up with Google (Google Identity Services ID tokens).

The frontend shows Google's button, receives a signed ID token (the
"credential") and posts it to `POST /api/auth/google/`. Here we:

1. Verify the token ourselves: Google's signature (public keys cached), issuer,
   audience (one of GOOGLE_OAUTH_CLIENT_IDS), expiry, and that Google has
   verified the email. Nothing in the token is trusted before that.
2. Find the user, in this order:
   * the Google account already linked (allauth `SocialAccount`, by Google's
     stable user id `sub`, so a later email change at Google doesn't matter);
   * an existing RIINOX account with the same email: **linked automatically**.
     Safe because Google proved the person owns that address; an account still
     waiting for email activation is activated by it;
   * nobody: a new, active, verified user with no password (they can set one
     later with "forgot password").
3. Return exactly what `/api/auth/jwt/login/` returns.

A deactivated account (verified, then switched off) is never revived by Google.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import requests
from allauth.account.models import EmailAddress
from allauth.socialaccount.models import SocialAccount
from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError, transaction
from google.auth import exceptions as google_exceptions
from google.auth import jwt as google_jwt
from rest_framework import status

from core.applications.notification.audit.action import AuditAction
from core.applications.notification.audit.services import record as audit_record
from core.applications.users.models import User
from core.helper.custom_exceptions import BusinessRuleError

logger = logging.getLogger(__name__)

PROVIDER = "google"
GOOGLE_CERTS_URL = "https://www.googleapis.com/oauth2/v1/certs"
GOOGLE_ISSUERS = frozenset({"accounts.google.com", "https://accounts.google.com"})
CERTS_CACHE_KEY = "auth:google:certs"
CERTS_CACHE_SECONDS = 60 * 60  # Google rotates keys every few days and publishes new ones in advance.
CLOCK_SKEW_SECONDS = 10

__all__ = ["GoogleSignIn", "GoogleSignInError", "sign_in_with_google", "verify_google_credential"]


class GoogleSignInError(BusinessRuleError):
    pass


def _fail(code: str, message: str, http_status: int = status.HTTP_401_UNAUTHORIZED) -> GoogleSignInError:
    return GoogleSignInError(code, message, status_code=http_status)


@dataclass(frozen=True, slots=True)
class GoogleSignIn:
    user: User
    created: bool  # a new RIINOX account
    linked: bool  # Google was attached to an existing account just now


# --------------------------------------------------------------------------- #
# Token verification
# --------------------------------------------------------------------------- #
def _client_ids() -> list[str]:
    return [cid for cid in getattr(settings, "GOOGLE_OAUTH_CLIENT_IDS", []) if cid]


def _google_certs(*, refresh: bool = False) -> dict[str, str]:
    if not refresh:
        certs = cache.get(CERTS_CACHE_KEY)
        if certs:
            return certs
    try:
        response = requests.get(GOOGLE_CERTS_URL, timeout=5)
        response.raise_for_status()
        certs = response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning("google_auth.certs_unavailable", exc_info=exc)
        raise _fail(
            "GOOGLE_UNAVAILABLE", "Couldn't reach Google to check the sign-in. Try again.",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        ) from exc
    cache.set(CERTS_CACHE_KEY, certs, CERTS_CACHE_SECONDS)
    return certs


def verify_google_credential(credential: str) -> dict[str, Any]:
    """The token's verified claims. Raises GoogleSignInError (401) for anything not genuine."""
    client_ids = _client_ids()
    if not client_ids:
        raise _fail(
            "GOOGLE_SIGN_IN_NOT_CONFIGURED", "Google sign-in isn't set up on this server.",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    claims = None
    for refresh in (False, True):
        try:
            claims = google_jwt.decode(
                credential,
                certs=_google_certs(refresh=refresh),
                audience=client_ids,
                clock_skew_in_seconds=CLOCK_SKEW_SECONDS,
            )
            break
        except (ValueError, google_exceptions.GoogleAuthError) as exc:
            # A key id we don't know yet means Google rotated keys: refetch once.
            if not refresh and "Certificate for key id" in str(exc):
                continue
            logger.info("google_auth.invalid_token", extra={"reason": str(exc)[:200]})
            raise _fail("GOOGLE_TOKEN_INVALID", "The Google sign-in couldn't be verified. Try again.") from exc

    if claims.get("iss") not in GOOGLE_ISSUERS:
        raise _fail("GOOGLE_TOKEN_INVALID", "The Google sign-in couldn't be verified. Try again.")
    if not claims.get("sub") or not claims.get("email"):
        raise _fail("GOOGLE_TOKEN_INVALID", "The Google sign-in couldn't be verified. Try again.")
    if claims.get("email_verified") not in (True, "true"):
        raise _fail(
            "GOOGLE_EMAIL_NOT_VERIFIED",
            "Your Google account's email isn't verified. Verify it with Google, or sign up with email.",
            status.HTTP_403_FORBIDDEN,
        )
    return claims


# --------------------------------------------------------------------------- #
# Finding, linking or creating the user
# --------------------------------------------------------------------------- #
def _name(claims: dict[str, Any]) -> str:
    name = (claims.get("name") or "").strip()
    if not name:
        name = " ".join(part for part in (claims.get("given_name"), claims.get("family_name")) if part).strip()
    return name[:255]


def _mark_email_verified(user: User, email: str) -> None:
    """Keep allauth's email records in step (used by its web pages and admin)."""
    address, _ = EmailAddress.objects.get_or_create(
        user=user, email__iexact=email, defaults={"email": email, "primary": True, "verified": True}
    )
    if not address.verified:
        address.verified = True
        address.save(update_fields=["verified"])


def _usable(user: User) -> User:
    if user.is_active:
        return user
    if user.is_verified:
        # Activated once, then switched off: a deliberate decision Google must not undo.
        raise _fail(
            "ACCOUNT_DISABLED", "This account has been deactivated. Contact support.", status.HTTP_403_FORBIDDEN
        )
    # Signed up with email but never clicked the activation link: Google just proved the address.
    user.is_active = True
    user.is_verified = True
    user.save(update_fields=["is_active", "is_verified"])
    return user


@transaction.atomic
def sign_in_with_google(credential: str, *, audit_context: Any = None) -> GoogleSignIn:
    claims = verify_google_credential(credential)
    google_id = str(claims["sub"])
    email = User.objects.normalize_email(claims["email"]).strip()

    linked_account = SocialAccount.objects.select_related("user").filter(provider=PROVIDER, uid=google_id).first()
    if linked_account is not None:
        user = _usable(linked_account.user)
        linked_account.extra_data = claims
        linked_account.save(update_fields=["extra_data", "last_login"])
        return GoogleSignIn(user=user, created=False, linked=False)

    user = User.objects.select_for_update().filter(email__iexact=email).first()
    created = user is None
    if created:
        try:
            with transaction.atomic():
                # No password: they sign in with Google, or set one later via "forgot password".
                user = User.objects.create_user(
                    email=email, password=None, name=_name(claims), is_active=True, is_verified=True
                )
        except IntegrityError:
            # The same person signed up twice at the same moment.
            user = User.objects.get(email__iexact=email)
            created = False
    else:
        user = _usable(user)
        if not user.name and _name(claims):
            user.name = _name(claims)
            user.save(update_fields=["name"])

    try:
        with transaction.atomic():
            SocialAccount.objects.create(user=user, provider=PROVIDER, uid=google_id, extra_data=claims)
    except IntegrityError:
        pass  # linked by a parallel request
    _mark_email_verified(user, email)

    audit_record(
        action=AuditAction.AUTH_GOOGLE_SIGNUP if created else AuditAction.AUTH_GOOGLE_LINKED,
        actor=user,
        resource=user,
        metadata={"provider": PROVIDER, "email": email},
        context=audit_context,
    )
    return GoogleSignIn(user=user, created=created, linked=not created)
