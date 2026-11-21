"""
One-time 6-digit codes sent by email (account activation, password reset,
email reset).

Each (user, purpose) has **at most one live code**:

* `make_token` creates a fresh random code, replacing any earlier one, so only
  the latest email works.
* The code is stored only as a keyed hash (HMAC with SECRET_KEY) in the cache,
  with its expiry and a wrong-guess counter.
* `check_token` accepts it once. After MAX_ATTEMPTS wrong guesses the code is
  destroyed and a new one must be requested, so guessing has at most
  MAX_ATTEMPTS / 1,000,000 chance per email sent (plus the endpoint rate limits).

The cache must be shared by every server process in production (it is Redis).
Attempts live in the cache rather than the database on purpose: a wrong guess
ends in an error response, which rolls back the request's database transaction
but not the cache, so the counter can't be reset by failing.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from typing import TYPE_CHECKING

from django.conf import settings
from django.core.cache import cache

if TYPE_CHECKING:
    from core.applications.users.models import User

ACTIVATION = "activation"
PASSWORD_RESET = "password_reset"
EMAIL_RESET = "email_reset"
PURPOSES = frozenset({ACTIVATION, PASSWORD_RESET, EMAIL_RESET})


class TokenGenerator:
    EXPIRY_SECONDS = 600  # 10 minutes
    MAX_ATTEMPTS = 5
    DIGITS = 6

    def _key(self, user: User, purpose: str) -> str:
        if purpose not in PURPOSES:
            msg = f"Unknown code purpose {purpose!r}."
            raise ValueError(msg)
        return f"auth:code:{purpose}:{user.pk}"

    def _hash(self, user: User, purpose: str, code: str) -> str:
        message = f"{purpose}:{user.pk}:{code}".encode()
        return hmac.new(settings.SECRET_KEY.encode(), message, hashlib.sha256).hexdigest()

    def make_token(self, user: User, purpose: str = ACTIVATION) -> str:
        """A new random code for this user and purpose. Any earlier code stops working."""
        code = f"{secrets.randbelow(10**self.DIGITS):0{self.DIGITS}d}"
        key = self._key(user, purpose)
        record = {"hash": self._hash(user, purpose, code), "expires_at": time.time() + self.EXPIRY_SECONDS}
        cache.set(key, record, self.EXPIRY_SECONDS)
        cache.set(f"{key}:attempts", 0, self.EXPIRY_SECONDS)
        return code

    def check_token(self, user: User, token: str, purpose: str = ACTIVATION) -> bool:
        """
        True once for the right, unexpired code; the code is then used up. A wrong
        guess counts towards MAX_ATTEMPTS, after which the code is destroyed.
        """
        if not (user and token and getattr(user, "pk", None)):
            return False
        key = self._key(user, purpose)
        attempts_key = f"{key}:attempts"
        record = cache.get(key)
        if not record or record["expires_at"] < time.time():
            cache.delete_many([key, attempts_key])
            return False

        # Count the try before comparing, atomically (Redis INCR), so parallel guesses
        # can't share one count.
        try:
            attempts = cache.incr(attempts_key)
        except ValueError:  # counter evicted: treat as exhausted
            attempts = self.MAX_ATTEMPTS + 1
        if attempts > self.MAX_ATTEMPTS:
            cache.delete_many([key, attempts_key])
            return False

        if hmac.compare_digest(record["hash"], self._hash(user, purpose, str(token).strip())):
            cache.delete_many([key, attempts_key])
            return True
        if attempts >= self.MAX_ATTEMPTS:
            cache.delete_many([key, attempts_key])
        return False


default_token_generator = TokenGenerator()
