import base64
from datetime import datetime

import pyotp
from django.conf import settings

from core.applications.users.models import User


class TokenGenerator:
    """
    Custom token generator using HOTP for user activation/verification.
    Always generates a clean 6-digit numeric token.
    """

    EXPIRY_SECONDS = 600  # 10 minutes

    key_salt = "django.contrib.auth.tokens.PasswordResetTokenGenerator"
    algorithm = None
    _secret = None

    def __init__(self):
        self.algorithm = self.algorithm or "sha256"

    def _get_secret(self):
        return self._secret or settings.SECRET_KEY

    def _set_secret(self, secret):
        self._secret = secret

    secret = property(_get_secret, _set_secret)

    def make_token(self, user: User):
        """
        Generate a token with exactly 6 digits using HOTP.
        Uses timestamp as the counter.
        """
        timestamp = self._num_seconds(self._now())
        key = base64.b32encode(self._make_hash_value(user, timestamp).encode())
        otp = pyotp.HOTP(key, digits=6).at(timestamp)
        return otp  # ✅ Always 6-digit string

    def check_token(self, user: User, token: str) -> bool:
        """
        Validate 6-digit token by checking HOTP counters within expiry window.
        """
        if not (user and token):
            return False

        now_ts = self._num_seconds(self._now())

        # Check within allowed window
        for delta in range(self.EXPIRY_SECONDS + 1):
            ts_try = now_ts - delta
            key = base64.b32encode(self._make_hash_value(user, ts_try).encode())
            otp = pyotp.HOTP(key, digits=6).at(ts_try)
            if otp == token:
                return True
        return False

    def _make_hash_value(self, user: User, timestamp: int):
        login_timestamp = (
            ""
            if user.last_login is None
            else user.last_login.replace(microsecond=0, tzinfo=None)
        )
        email_field = user.get_email_field_name()
        email = getattr(user, email_field, "") or ""
        return f"{user.pk}{user.password}{login_timestamp}{timestamp}{email}"

    def _num_seconds(self, dt: datetime) -> int:
        return int((dt - datetime(2001, 1, 1)).total_seconds())

    def _now(self) -> datetime:
        return datetime.now()


default_token_generator = TokenGenerator()
