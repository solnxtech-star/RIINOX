"""
OpenAPI documentation for `UserViewSet` (`/api/users/...`): sign-up, activation,
passwords, email changes and the signed-in user's profile.

Behaviour notes the frontend needs (all verified against the code):

* Every code sent by email (activation, password reset, email reset) is a
  **6-digit code valid for 10 minutes**. The emails contain the code only, no
  link, so the app shows a "enter the code" screen. Each request is checked with
  the account's **email + code**.
* A code works **once**, only the **latest** email's code works, and it dies after
  5 wrong tries (request a new one). Sending and checking codes is rate limited
  per IP (429).
* Signing in returns JWTs (`/api/auth/jwt/login/` or `/api/auth/google/`); send
  `Authorization: Bearer <access>`. Access tokens last 60 minutes; refreshing
  returns a NEW refresh token every time (store it; the old one stops working).
"""

from __future__ import annotations

from drf_spectacular.utils import OpenApiExample
from drf_spectacular.utils import OpenApiResponse
from drf_spectacular.utils import extend_schema
from drf_spectacular.utils import extend_schema_view
from drf_spectacular.utils import inline_serializer
from rest_framework import serializers

AUTH = ["auth"]
USER = ["User"]

_CODE_NOTE = (
    "The code is the **6-digit number** from the email. It is valid for **10 minutes**, works **once**, and only "
    "the code from the **latest** email works. After **5 wrong tries** it is destroyed: request a new one. "
    "Limited to 10 tries a minute per IP address (`429`)."
)
_SEND_LIMIT = "Limited to 5 requests a minute per IP address (`429`)."

_LOGIN_PAYLOAD = inline_serializer(
    name="UserAuthPayload",
    fields={
        "access": serializers.CharField(),
        "refresh": serializers.CharField(),
        "setup_info": inline_serializer(
            name="UserAuthPayloadUser",
            fields={"id": serializers.IntegerField(), "email": serializers.EmailField(), "name": serializers.CharField()},
        ),
        "registration_complete": serializers.BooleanField(),
    },
)

_ME = inline_serializer(
    name="CurrentUser",
    fields={
        "id": serializers.IntegerField(read_only=True),
        "email": serializers.EmailField(read_only=True, help_text="Change it with POST /api/users/set_email/."),
        "name": serializers.CharField(),
        "role": serializers.CharField(
            read_only=True, allow_null=True,
            help_text="Name of the role in the user's oldest active membership; null with no organization.",
        ),
        "is_active": serializers.BooleanField(read_only=True),
    },
)

_ME_EXAMPLE = {"id": 7, "email": "ada@shop.ng", "name": "Ada Obi", "role": "Owner", "is_active": True}

_EMAIL_ONLY = inline_serializer(name="EmailOnly", fields={"email": serializers.EmailField()})

_NO_CONTENT = OpenApiResponse(description="Done. No body.")
_BAD_CODE = OpenApiResponse(
    description="`{\"token\": \"The token may have expired or is invalid.\"}`: wrong, expired, already used or "
    "replaced code, or an email with no account (same answer, so accounts can't be discovered)."
)
_TOO_MANY = OpenApiResponse(description="Too many attempts from this address; wait a minute.")


user_viewset_schema = extend_schema_view(
    # ------------------------------------------------------------------ sign-up
    create=extend_schema(
        tags=AUTH,
        summary="Sign up with email and password",
        description=(
            "Creates an account and emails a **6-digit activation code**. The account can't sign in until it is "
            "activated with `POST /api/users/activation/`.\n\n"
            "* `password` must pass the password rules (length, not too common, not all numbers, not too similar "
            "to the email). `re_password` must match.\n"
            "* An email that is already registered is a `400` on `email`.\n"
            "* Prefer Google? `POST /api/auth/google/` signs up and in at once, with no activation step.\n\n"
            + _SEND_LIMIT + " No authentication needed."
        ),
        examples=[
            OpenApiExample(
                "Sign up",
                request_only=True,
                value={"name": "Ada Obi", "email": "ada@shop.ng", "password": "Str0ng-pass!", "re_password": "Str0ng-pass!"},
            ),
            OpenApiExample(
                "Created", response_only=True, status_codes=["201"],
                value={"id": 7, "name": "Ada Obi", "email": "ada@shop.ng"},
            ),
        ],
        responses={
            201: OpenApiResponse(description="Account created; activation code sent."),
            400: OpenApiResponse(description="Validation error (email taken, weak password, passwords differ)."),
            500: OpenApiResponse(description="`EMAIL_ERROR`: the activation email couldn't be sent."),
        },
    ),
    activation=extend_schema(
        tags=AUTH,
        summary="Activate the account with the emailed code",
        description=(
            "Activates and verifies the account, then **signs the user in**: the response is the same as "
            "`POST /api/auth/jwt/login/`, so store the tokens and continue to onboarding.\n\n"
            + _CODE_NOTE + "\n\n"
            "* An account that is already active is `403` (the code was already used). Send them to login.\n"
            "* Wrong or expired code: `400` on `token`. Offer \"resend code\" (`POST /api/users/resend_activation/`).\n\n"
            "No authentication needed."
        ),
        examples=[OpenApiExample("Activate", request_only=True, value={"email": "ada@shop.ng", "token": "482913"})],
        responses={
            200: _LOGIN_PAYLOAD, 400: _BAD_CODE, 403: OpenApiResponse(description="Already activated."), 429: _TOO_MANY,
        },
    ),
    resend_activation=extend_schema(
        tags=AUTH,
        summary="Resend the activation code",
        description=(
            "Emails a new activation code to an account that **isn't activated yet**.\n\n"
            "* `204`: sent.\n"
            "* `400` with no body: there's no inactive account with this email (unknown, or already active). Show "
            "a neutral message such as \"If this email needs activating, a code is on its way\".\n\n"
            "Logging in with the password of an unactivated account also resends the code (and answers `403`).\n\n"
            "Sending a new code cancels the previous one. " + _SEND_LIMIT + " No authentication needed."
        ),
        request=_EMAIL_ONLY,
        examples=[OpenApiExample("Resend", request_only=True, value={"email": "ada@shop.ng"})],
        responses={
            204: _NO_CONTENT, 400: OpenApiResponse(description="No inactive account with this email."), 429: _TOO_MANY,
        },
    ),
    # ---------------------------------------------------------- forgot password
    reset_password=extend_schema(
        tags=AUTH,
        summary="Forgot password: email a reset code",
        description=(
            "Emails a **6-digit reset code** if an active account uses this email. Always answers `204`, whether or "
            "not the email exists, so the form can't be used to discover accounts.\n\n"
            "Next step: `POST /api/users/reset_password_confirm/` with the email, the code and the new password.\n\n"
            "Also the way for a **Google-only user** (no password) to create a password.\n\n"
            + _SEND_LIMIT + " No authentication needed."
        ),
        request=_EMAIL_ONLY,
        examples=[OpenApiExample("Forgot", request_only=True, value={"email": "ada@shop.ng"})],
        responses={204: _NO_CONTENT, 429: _TOO_MANY},
    ),
    reset_password_confirm=extend_schema(
        tags=AUTH,
        summary="Forgot password: set a new password with the code",
        description=(
            "Sets a new password using the emailed code, and emails a \"password changed\" notice.\n\n"
            + _CODE_NOTE + "\n\n"
            "* `new_password` must pass the password rules; `re_new_password` must match.\n"
            "* The user is **not** signed in by this call: send them to login with the new password.\n"
            "* **Every existing session is logged out** (all refresh tokens revoked), so whoever had the account "
            "is locked out.\n\n"
            "No authentication needed."
        ),
        examples=[
            OpenApiExample(
                "Reset",
                request_only=True,
                value={"email": "ada@shop.ng", "token": "482913", "new_password": "N3w-pass!", "re_new_password": "N3w-pass!"},
            )
        ],
        responses={204: _NO_CONTENT, 400: _BAD_CODE, 429: _TOO_MANY},
    ),
    # --------------------------------------------------------- signed-in user
    me=[
        extend_schema(
            methods=["GET"],
            tags=USER,
            summary="My profile",
            description=(
                "The signed-in user. `role` is the role name in their first active organization (null if none); for "
                "per-organization roles and permissions use `GET /api/organizations/` and the membership endpoints."
            ),
            responses={200: _ME},
            examples=[OpenApiExample("Me", value=_ME_EXAMPLE, response_only=True)],
        ),
        extend_schema(
            methods=["PATCH"],
            tags=USER,
            summary="Update my profile",
            description=(
                "Change `name`. Send only what changes.\n\n"
                "`email` and `is_active` are read-only here (ignored if sent). Change the email with "
                "`POST /api/users/set_email/`, which checks the password."
            ),
            request=inline_serializer(name="CurrentUserPatch", fields={"name": serializers.CharField(required=False)}),
            examples=[OpenApiExample("Rename", request_only=True, value={"name": "Ada N. Obi"})],
            responses={200: _ME},
        ),
        extend_schema(
            methods=["PUT"],
            tags=USER,
            summary="Replace my profile",
            description="Like PATCH but every writable field is required. Prefer PATCH.",
            responses={200: _ME},
        ),
        extend_schema(
            methods=["DELETE"],
            tags=USER,
            summary="Delete my account",
            description=(
                "Permanently deletes the signed-in user after checking `current_password`. Throw away the stored "
                "tokens afterwards.\n\n"
                "* Wrong password: `400` on `current_password`.\n"
                "* Users who own records that must be kept (for example an organization's history) get "
                "`409 PROTECTED_ERROR`: deactivate their memberships instead.\n"
                "* Google-only users (no password) must first set one with \"Forgot password\"."
            ),
            request=inline_serializer(name="DeleteMe", fields={"current_password": serializers.CharField()}),
            responses={
                204: _NO_CONTENT,
                400: OpenApiResponse(description="Wrong password."),
                409: OpenApiResponse(description="`PROTECTED_ERROR`."),
            },
        ),
    ],
    set_password=extend_schema(
        tags=USER,
        summary="Change my password",
        description=(
            "For a signed-in user who knows their current password. Emails a \"password changed\" notice.\n\n"
            "* **All sessions are logged out**, including this one: refresh tokens stop working, and the current "
            "access token works until it expires (at most 60 minutes). Send the user to login again.\n"
            "* Wrong `current_password`: `400`.\n"
            "* `new_password` must pass the password rules; `re_new_password` must match.\n"
            "* Google-only users have no current password: use \"Forgot password\" instead."
        ),
        examples=[
            OpenApiExample(
                "Change", request_only=True,
                value={"current_password": "Old-pass!", "new_password": "N3w-pass!", "re_new_password": "N3w-pass!"},
            )
        ],
        responses={
            204: _NO_CONTENT, 400: OpenApiResponse(description="Wrong password or invalid new password."),
            429: _TOO_MANY,
        },
    ),
    set_username=extend_schema(
        tags=USER,
        summary="Change my email (with password)",
        description=(
            "Changes the sign-in email of the signed-in user after checking `current_password`, and emails a "
            "confirmation. The new email must not belong to another account (`400`). From then on, sign in with the "
            "new email."
        ),
        examples=[
            OpenApiExample("Change email", request_only=True, value={"current_password": "Pa55-word!", "new_email": "ada@newshop.ng"})
        ],
        responses={
            204: _NO_CONTENT, 400: OpenApiResponse(description="Wrong password, or email already used."),
            429: _TOO_MANY,
        },
    ),
    reset_username=extend_schema(
        tags=AUTH,
        summary="Email reset: send a code",
        description=(
            "For users who can't use their old email any more but still receive mail at it: emails a 6-digit code to "
            "the account's **current** email. Always `204`, so it can't be used to discover accounts.\n\n"
            "Next step: `POST /api/users/reset_email_confirm/`.\n\n" + _SEND_LIMIT + " No authentication needed."
        ),
        request=_EMAIL_ONLY,
        examples=[OpenApiExample("Send code", request_only=True, value={"email": "ada@shop.ng"})],
        responses={204: _NO_CONTENT, 429: _TOO_MANY},
    ),
    reset_username_confirm=extend_schema(
        tags=AUTH,
        summary="Email reset: set the new email with the code",
        description=(
            "Changes the account's email using the code sent by `POST /api/users/reset_email/`.\n\n"
            + _CODE_NOTE + "\n\n"
            "`email` is the account's **current** email (the one the code was sent to); `new_email` must not belong "
            "to another account.\n\nNo authentication needed."
        ),
        request=inline_serializer(
            name="EmailResetConfirm",
            fields={
                "email": serializers.EmailField(help_text="The current email (where the code was sent)."),
                "token": serializers.CharField(help_text="The 6-digit code."),
                "new_email": serializers.EmailField(),
            },
        ),
        examples=[
            OpenApiExample(
                "Confirm", request_only=True, value={"email": "ada@shop.ng", "token": "482913", "new_email": "ada@newshop.ng"}
            )
        ],
        responses={204: _NO_CONTENT, 400: _BAD_CODE, 429: _TOO_MANY},
    ),
    # ----------------------------------------------------------- user records
    list=extend_schema(
        tags=USER,
        summary="Users (yourself only)",
        description=(
            "Returns **only the signed-in user** (as a one-item page) unless the caller is platform staff. To list the "
            "people in an organization use `GET /api/organizations/{id}/members/`."
        ),
    ),
    retrieve=extend_schema(
        tags=USER,
        summary="A user by id (yourself only)",
        description="Only your own id works (`404` otherwise, unless platform staff). Prefer `GET /api/users/me/`.",
    ),
    update=extend_schema(
        tags=USER, summary="Replace a user (yourself only)",
        description="Same as `PUT /api/users/me/`. Prefer `PATCH /api/users/me/`.",
    ),
    partial_update=extend_schema(
        tags=USER, summary="Update a user (yourself only)",
        description="Same as `PATCH /api/users/me/`, which is the one to use.",
    ),
    destroy=extend_schema(
        tags=USER, summary="Delete a user (yourself only)",
        description="Same as `DELETE /api/users/me/`; needs `current_password`. Prefer the `/me/` form.",
        request=inline_serializer(name="DeleteUser", fields={"current_password": serializers.CharField()}),
        responses={204: _NO_CONTENT},
    ),
    # ------------------------------------------------------------------ misc
    logout=[
        extend_schema(
            methods=["POST"],
            tags=AUTH,
            summary="Log out (this device or everywhere)",
            description=(
                "Revokes the session; then **delete the stored `access` and `refresh` tokens** in the app.\n\n"
                "* `{\"refresh\": \"<refresh token>\"}`: logs out this device (that refresh token stops working).\n"
                "* `{\"all\": true}`: logs out **every device** of this user.\n\n"
                "Access tokens can't be revoked: one already issued keeps working until it expires (at most 60 "
                "minutes), which is why the app must also delete it. Logging out twice is fine.\n\n"
                "Needs `Authorization: Bearer <access>`."
            ),
            request=inline_serializer(
                name="LogoutRequest",
                fields={
                    "refresh": serializers.CharField(required=False),
                    "all": serializers.BooleanField(required=False),
                },
            ),
            examples=[
                OpenApiExample("This device", request_only=True, value={"refresh": "eyJ..."}),
                OpenApiExample("Everywhere", request_only=True, value={"all": True}),
            ],
            responses={
                204: _NO_CONTENT,
                400: OpenApiResponse(description="No `refresh`/`all`, or a refresh token that isn't valid or yours."),
            },
        ),
        extend_schema(
            methods=["GET"],
            tags=AUTH,
            summary="Log out (old form)",
            description="Revokes nothing. Kept for old clients; use `POST` with the refresh token.",
            deprecated=True,
            responses={204: _NO_CONTENT},
        ),
    ],
)
