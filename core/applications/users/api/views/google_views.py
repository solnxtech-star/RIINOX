from __future__ import annotations

from drf_spectacular.utils import OpenApiExample
from drf_spectacular.utils import OpenApiResponse
from drf_spectacular.utils import extend_schema
from drf_spectacular.utils import inline_serializer
from rest_framework import serializers
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from core.applications.notification.audit.context import AuditContext
from core.applications.users.api.serializers.user_serializers import (
    CustomTokenObtainPairSerializer,
)
from core.applications.users.auth_utils import build_auth_payload
from core.applications.users.google_auth import sign_in_with_google


class GoogleLoginSerializer(serializers.Serializer):
    credential = serializers.CharField(
        max_length=4096,
        trim_whitespace=True,
        help_text="The ID token Google Identity Services gives the frontend (`response.credential`).",
    )


GoogleLoginResponse = inline_serializer(
    name="GoogleLoginResponse",
    fields={
        "access": serializers.CharField(),
        "refresh": serializers.CharField(),
        "setup_info": inline_serializer(
            name="GoogleLoginUser",
            fields={"id": serializers.IntegerField(), "email": serializers.EmailField(), "name": serializers.CharField()},
        ),
        "registration_complete": serializers.BooleanField(),
        "is_new_user": serializers.BooleanField(),
    },
)

_DESCRIPTION = """
Signs a user **in or up** with Google. One button does both.

**Frontend flow**
1. Show Google's button with Google Identity Services, using the same client id as the backend's
   `GOOGLE_OAUTH_CLIENT_IDS`.
2. Google calls your callback with `response.credential`, a signed ID token.
3. Send it here as `{"credential": "<token>"}`.
4. Store `access` / `refresh` exactly as after `POST /api/auth/jwt/login/`. The response has the same shape,
   plus `is_new_user`.
5. Then continue as after a normal login: a new user (or anyone without an organization) goes to
   "Create organization", and pending invitations work as usual.

**Who you are**
* The Google account is already linked: you're signed in to that RIINOX account.
* A RIINOX account already uses this email: Google is **linked to it automatically** and you're signed in
  (Google has verified the email). An account still waiting for its activation email is activated.
* Otherwise a new account is created (`is_new_user: true`), already verified, with your Google name and
  **no password**. To also sign in with email and password later, use "Forgot password" to set one.

**Errors**
* `401 GOOGLE_TOKEN_INVALID`: the token is forged, expired, or for another app. Ask Google again.
* `403 GOOGLE_EMAIL_NOT_VERIFIED`: the Google account's email isn't verified.
* `403 ACCOUNT_DISABLED`: the RIINOX account was deactivated; Google can't reactivate it.
* `503 GOOGLE_SIGN_IN_NOT_CONFIGURED` (server has no client id) or `GOOGLE_UNAVAILABLE` (Google unreachable).
* `429`: more than 20 attempts a minute from one address.

No authentication header is needed.
"""


class GoogleLoginView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "google_login"

    @extend_schema(
        tags=["auth"],
        summary="Sign in or sign up with Google",
        description=_DESCRIPTION,
        request=GoogleLoginSerializer,
        responses={
            200: OpenApiResponse(GoogleLoginResponse, description="Signed in (existing account)."),
            201: OpenApiResponse(GoogleLoginResponse, description="Signed up (new account)."),
            401: OpenApiResponse(description="`GOOGLE_TOKEN_INVALID`."),
            403: OpenApiResponse(description="`GOOGLE_EMAIL_NOT_VERIFIED` or `ACCOUNT_DISABLED`."),
            503: OpenApiResponse(description="`GOOGLE_SIGN_IN_NOT_CONFIGURED` or `GOOGLE_UNAVAILABLE`."),
        },
        examples=[
            OpenApiExample("Request", request_only=True, value={"credential": "eyJhbGciOiJSUzI1NiIsImtpZCI6..."}),
            OpenApiExample(
                "New user",
                response_only=True,
                status_codes=["201"],
                value={
                    "access": "eyJ...",
                    "refresh": "eyJ...",
                    "setup_info": {"id": 42, "email": "ada@gmail.com", "name": "Ada Obi"},
                    "registration_complete": True,
                    "is_new_user": True,
                },
            ),
        ],
    )
    def post(self, request, *args, **kwargs):
        serializer = GoogleLoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = sign_in_with_google(
            serializer.validated_data["credential"], audit_context=AuditContext.from_request(request)
        )
        data = build_auth_payload(result.user, CustomTokenObtainPairSerializer)
        data["is_new_user"] = result.created
        return Response(data, status=status.HTTP_201_CREATED if result.created else status.HTTP_200_OK)
