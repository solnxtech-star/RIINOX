"""Sign in / sign up with Google: token checks, linking, account states, response shape.

Tokens are real RS256 JWTs signed with a throwaway key; the "Google certs" are that
key's certificate, served from the cache, so nothing calls Google.
"""

from __future__ import annotations

import datetime as dt
import time

from allauth.account.models import EmailAddress
from allauth.socialaccount.models import SocialAccount
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from django.core.cache import cache
from django.test import TestCase
from django.test import override_settings
from google.auth import crypt
from google.auth import jwt as google_jwt
from rest_framework.test import APIClient

from core.applications.notification.audit.action import AuditAction
from core.applications.notification.models import AuditLog
from core.applications.users.google_auth import CERTS_CACHE_KEY
from core.applications.users.models import User

CLIENT_ID = "riinox-test.apps.googleusercontent.com"
URL = "/api/auth/google/"


def _key_and_cert():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test")])
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    pem_key = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    return pem_key, cert.public_bytes(serialization.Encoding.PEM).decode()


PEM_KEY, PEM_CERT = _key_and_cert()
OTHER_KEY, _ = _key_and_cert()


def credential(*, key=PEM_KEY, **overrides):
    now = int(time.time())
    claims = {
        "iss": "https://accounts.google.com",
        "aud": CLIENT_ID,
        "sub": "1098765432101234567890",
        "email": "ada@gmail.com",
        "email_verified": True,
        "name": "Ada Obi",
        "iat": now,
        "exp": now + 3600,
        **overrides,
    }
    signer = crypt.RSASigner.from_string(key, key_id="test-key")
    return google_jwt.encode(signer, claims).decode()


@override_settings(GOOGLE_OAUTH_CLIENT_IDS=[CLIENT_ID])
class GoogleLoginTests(TestCase):
    def setUp(self):
        cache.set(CERTS_CACHE_KEY, {"test-key": PEM_CERT}, 3600)
        self.client = APIClient()

    def tearDown(self):
        cache.delete(CERTS_CACHE_KEY)

    def _login(self, token=None):
        return self.client.post(URL, {"credential": token or credential()}, format="json")

    # ---------------------------------------------------------------- sign up
    def test_new_user_is_created_verified_without_password(self):
        response = self._login()
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertTrue(body["is_new_user"])
        self.assertTrue(body["access"] and body["refresh"])
        self.assertEqual(body["setup_info"]["email"], "ada@gmail.com")
        self.assertTrue(body["registration_complete"])

        user = User.objects.get(email="ada@gmail.com")
        self.assertEqual(user.name, "Ada Obi")
        self.assertTrue(user.is_active and user.is_verified)
        self.assertFalse(user.has_usable_password())
        self.assertTrue(SocialAccount.objects.filter(user=user, provider="google", uid="1098765432101234567890").exists())
        self.assertTrue(EmailAddress.objects.filter(user=user, verified=True).exists())
        self.assertTrue(AuditLog.objects.filter(action=AuditAction.AUTH_GOOGLE_SIGNUP).exists())

        # The access token works on the API.
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {body['access']}")
        self.assertEqual(self.client.get("/api/users/me/").status_code, 200)

    def test_second_sign_in_returns_same_account(self):
        self._login()
        response = self._login()
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["is_new_user"])
        self.assertEqual(User.objects.count(), 1)

    def test_linked_by_google_id_even_if_email_changes_at_google(self):
        self._login()
        response = self._login(credential(email="ada.new@gmail.com"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["setup_info"]["email"], "ada@gmail.com")
        self.assertEqual(User.objects.count(), 1)

    # ------------------------------------------------------------ auto-linking
    def test_existing_password_account_is_linked_automatically(self):
        user = User.objects.create_user(email="Ada@Gmail.com", password="S3cret-pass!", is_verified=True)
        response = self._login()
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(response.json()["is_new_user"])
        self.assertEqual(response.json()["setup_info"]["id"], user.pk)
        self.assertTrue(SocialAccount.objects.filter(user=user, provider="google").exists())
        user.refresh_from_db()
        self.assertTrue(user.has_usable_password())  # email login still works
        self.assertTrue(AuditLog.objects.filter(action=AuditAction.AUTH_GOOGLE_LINKED).exists())

    def test_pending_activation_is_activated(self):
        user = User.objects.create_user(email="ada@gmail.com", password="x", is_active=False, is_verified=False)
        self.assertEqual(self._login().status_code, 200)
        user.refresh_from_db()
        self.assertTrue(user.is_active and user.is_verified)

    def test_deactivated_account_is_refused(self):
        User.objects.create_user(email="ada@gmail.com", password="x", is_active=False, is_verified=True)
        response = self._login()
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "ACCOUNT_DISABLED")
        self.assertFalse(SocialAccount.objects.exists())

    # ------------------------------------------------------------ token checks
    def test_bad_tokens_are_rejected(self):
        cases = {
            "wrong audience": credential(aud="someone-else.apps.googleusercontent.com"),
            "expired": credential(iat=int(time.time()) - 7200, exp=int(time.time()) - 3600),
            "wrong issuer": credential(iss="https://evil.example.com"),
            "forged signature": credential(key=OTHER_KEY),
            "garbage": "not-a-jwt",
        }
        for label, token in cases.items():
            response = self._login(token)
            self.assertEqual(response.status_code, 401, label)
            self.assertEqual(response.json()["code"], "GOOGLE_TOKEN_INVALID", label)
        self.assertFalse(User.objects.exists())

    def test_unverified_google_email_is_refused(self):
        response = self._login(credential(email_verified=False))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "GOOGLE_EMAIL_NOT_VERIFIED")
        self.assertFalse(User.objects.exists())

    def test_missing_credential_is_400(self):
        self.assertEqual(self.client.post(URL, {}, format="json").status_code, 400)

    @override_settings(GOOGLE_OAUTH_CLIENT_IDS=[])
    def test_not_configured(self):
        response = self._login()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["code"], "GOOGLE_SIGN_IN_NOT_CONFIGURED")
