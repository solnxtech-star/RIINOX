"""UserViewSet endpoints behave as documented (core/applications/users/api/schemas/users.py)."""

from __future__ import annotations

from django.core.cache import cache
from django.test import TestCase
from django.test import override_settings
from rest_framework.test import APIClient

from core.applications.users.models import User
from core.applications.users.token import ACTIVATION
from core.applications.users.token import PASSWORD_RESET
from core.applications.users.token import default_token_generator as codes

PASSWORD = "Pa55-word-long!"
NEW_PASSWORD = "N3w-pass-long!"


# The locmem test backend deep-copies each email, and Djoser's emails hold the request,
# which can't be copied. Production sends through Brevo, which doesn't copy.
@override_settings(EMAIL_BACKEND="django.core.mail.backends.dummy.EmailBackend")
class UserEndpointTestBase(TestCase):
    def setUp(self):
        cache.clear()  # rate-limit counters and codes live in the cache
        self.user = User.objects.create_user(email="ada@shop.ng", password=PASSWORD, is_verified=True)
        self.client = APIClient()

    def _login(self, password=PASSWORD):
        return self.client.post("/api/auth/jwt/login/", {"email": "ada@shop.ng", "password": password}, format="json")

    def _reset_body(self, code, **extra):
        return {"email": "ada@shop.ng", "token": code, "new_password": NEW_PASSWORD,
                "re_new_password": NEW_PASSWORD, **extra}


class CodeTests(UserEndpointTestBase):
    def test_code_works_once_and_only_the_latest(self):
        old = codes.make_token(self.user, PASSWORD_RESET)
        latest = codes.make_token(self.user, PASSWORD_RESET)
        url = "/api/users/reset_password_confirm/"
        if old != latest:
            self.assertEqual(self.client.post(url, self._reset_body(old), format="json").status_code, 400)
        self.assertEqual(self.client.post(url, self._reset_body(latest), format="json").status_code, 204)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(NEW_PASSWORD))
        # Used up.
        self.assertEqual(self.client.post(url, self._reset_body(latest), format="json").status_code, 400)

    def test_five_wrong_tries_destroy_the_code(self):
        code = codes.make_token(self.user, PASSWORD_RESET)
        wrong = "000000" if code != "000000" else "111111"
        for _ in range(5):
            self.assertFalse(codes.check_token(self.user, wrong, PASSWORD_RESET))
        self.assertFalse(codes.check_token(self.user, code, PASSWORD_RESET))  # right code, but too late

    def test_codes_are_per_purpose(self):
        activation_code = codes.make_token(self.user, ACTIVATION)
        self.assertFalse(codes.check_token(self.user, activation_code, PASSWORD_RESET))

    def test_unknown_email_looks_like_a_wrong_code(self):
        body = {**self._reset_body("123456"), "email": "nobody@shop.ng"}
        response = self.client.post("/api/users/reset_password_confirm/", body, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"token": "The token may have expired or is invalid."})

    def test_checking_codes_is_rate_limited(self):
        url = "/api/users/reset_password_confirm/"
        statuses = [self.client.post(url, self._reset_body("000000"), format="json").status_code for _ in range(11)]
        self.assertEqual(statuses[-1], 429)

    def test_sending_codes_is_rate_limited(self):
        statuses = [
            self.client.post("/api/users/reset_password/", {"email": "ada@shop.ng"}, format="json").status_code
            for _ in range(6)
        ]
        self.assertEqual(statuses[:5], [204] * 5)
        self.assertEqual(statuses[5], 429)


class FlowTests(UserEndpointTestBase):
    def test_forgot_password_never_reveals_accounts_and_logs_out_everywhere(self):
        refresh = self._login().json()["refresh"]
        self.assertEqual(
            self.client.post("/api/users/reset_password/", {"email": "nobody@shop.ng"}, format="json").status_code, 204
        )
        code = codes.make_token(self.user, PASSWORD_RESET)
        self.assertEqual(
            self.client.post("/api/users/reset_password_confirm/", self._reset_body(code), format="json").status_code,
            204,
        )
        # The session from before the reset is gone.
        self.assertEqual(self.client.post("/api/auth/jwt/refresh/", {"refresh": refresh}, format="json").status_code, 401)

    def test_signup_then_activation_signs_in(self):
        response = self.client.post(
            "/api/users/",
            {"name": "Bola", "email": "bola@shop.ng", "password": PASSWORD, "re_password": PASSWORD},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        user = User.objects.get(email="bola@shop.ng")
        user.is_active = False  # what the activation flow expects before activation
        user.save(update_fields=["is_active"])

        code = codes.make_token(user, ACTIVATION)
        response = self.client.post("/api/users/activation/", {"email": "bola@shop.ng", "token": code}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json()["access"])
        user.refresh_from_db()
        self.assertTrue(user.is_active and user.is_verified)

    def test_change_email_with_password(self):
        self.client.force_authenticate(self.user)
        response = self.client.post(
            "/api/users/set_email/", {"current_password": PASSWORD, "new_email": "ada@newshop.ng"}, format="json"
        )
        self.assertEqual(response.status_code, 204, response.content)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "ada@newshop.ng")
        wrong = self.client.post(
            "/api/users/set_email/", {"current_password": "nope", "new_email": "x@shop.ng"}, format="json"
        )
        self.assertEqual(wrong.status_code, 400)


class ProfileTests(UserEndpointTestBase):
    def test_me_and_list_show_only_yourself(self):
        User.objects.create_user(email="other@shop.ng", password=PASSWORD)
        self.client.force_authenticate(self.user)
        self.assertEqual(self.client.get("/api/users/me/").json()["email"], "ada@shop.ng")
        self.assertEqual(self.client.get("/api/users/").json()["count"], 1)
        self.assertEqual(self.client.patch("/api/users/me/", {"name": "Ada"}, format="json").json()["name"], "Ada")

    def test_email_and_is_active_are_read_only_on_profile(self):
        self.client.force_authenticate(self.user)
        response = self.client.patch(
            "/api/users/me/", {"is_active": False, "email": "evil@x.com", "name": "Ada"}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_active)
        self.assertEqual(self.user.email, "ada@shop.ng")
        self.assertEqual(self.user.name, "Ada")

    def test_metadatas_endpoint_is_gone(self):
        self.client.force_authenticate(self.user)
        # No such action any more: the path is read as a user id, which can't be POSTed to.
        self.assertIn(self.client.post("/api/users/metadatas/", {}, format="json").status_code, (404, 405))


class SessionTests(UserEndpointTestBase):
    def _auth(self, access):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")

    def test_refresh_rotates_and_old_refresh_dies(self):
        tokens = self._login().json()
        first = self.client.post("/api/auth/jwt/refresh/", {"refresh": tokens["refresh"]}, format="json")
        self.assertEqual(first.status_code, 200, first.content)
        self.assertIn("refresh", first.json())
        again = self.client.post("/api/auth/jwt/refresh/", {"refresh": tokens["refresh"]}, format="json")
        self.assertEqual(again.status_code, 401)

    def test_logout_this_device(self):
        tokens = self._login().json()
        self._auth(tokens["access"])
        self.assertEqual(
            self.client.post("/api/users/logout/", {"refresh": tokens["refresh"]}, format="json").status_code, 204
        )
        self.client.credentials()
        self.assertEqual(
            self.client.post("/api/auth/jwt/refresh/", {"refresh": tokens["refresh"]}, format="json").status_code, 401
        )

    def test_logout_everywhere_and_password_change_end_all_sessions(self):
        phone = self._login().json()
        laptop = self._login().json()
        self._auth(laptop["access"])
        self.assertEqual(self.client.post("/api/users/logout/", {"all": True}, format="json").status_code, 204)
        self.client.credentials()
        for session in (phone, laptop):
            self.assertEqual(
                self.client.post("/api/auth/jwt/refresh/", {"refresh": session["refresh"]}, format="json").status_code,
                401,
            )

        tablet = self._login().json()
        self._auth(tablet["access"])
        response = self.client.post(
            "/api/users/set_password/",
            {"current_password": PASSWORD, "new_password": NEW_PASSWORD, "re_new_password": NEW_PASSWORD},
            format="json",
        )
        self.assertEqual(response.status_code, 204, response.content)
        self.client.credentials()
        self.assertEqual(
            self.client.post("/api/auth/jwt/refresh/", {"refresh": tablet["refresh"]}, format="json").status_code, 401
        )

    def test_cannot_revoke_someone_elses_token(self):
        other = User.objects.create_user(email="other@shop.ng", password=PASSWORD, is_verified=True)
        other_refresh = self.client.post(
            "/api/auth/jwt/login/", {"email": "other@shop.ng", "password": PASSWORD}, format="json"
        ).json()["refresh"]
        self._auth(self._login().json()["access"])
        self.assertEqual(
            self.client.post("/api/users/logout/", {"refresh": other_refresh}, format="json").status_code, 400
        )
        self.assertIsNotNone(other)
