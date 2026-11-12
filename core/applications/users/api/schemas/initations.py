from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample
from drf_spectacular.utils import OpenApiParameter
from drf_spectacular.utils import OpenApiResponse
from drf_spectacular.utils import extend_schema
from drf_spectacular.utils import extend_schema_view

from core.applications.users.api.schemas.common import ERROR_FORMAT_NOTE
from core.applications.users.api.schemas.common import LOCATION_OPTION_FIELDS
from core.applications.users.api.schemas.common import LOCATION_SCOPE_FIELDS
from core.applications.users.api.schemas.common import LOCATION_SCOPE_RULES
from core.applications.users.api.schemas.common import ORGANIZATION_ID_PARAM
from core.applications.users.api.schemas.common import RETRY_AFTER
from core.applications.users.api.schemas.common import TEAM_AUTH_NOTE
from core.applications.users.api.schemas.common import TOO_MANY_REQUESTS
from core.applications.users.api.schemas.common import VALIDATION_ERROR
from core.applications.users.api.schemas.common import coded_error
from core.applications.users.api.schemas.common import error_codes
from core.applications.users.api.serializers import (
    organization_serializers as org_serializers,
)
from core.applications.users.errors import InvitationErrorCode as Err

PUBLIC_TAGS = ["Invitations"]
TEAM_TAGS = ["Team invitations"]

_LOCATION_EXAMPLE = {
    "id": "a1b2c3d4-0000-4000-8000-000000000001",
    "name": "Lekki Branch",
    "code": "LEK",
    "location_type": "store",
    "is_default": False,
}

_INVITATION_EXAMPLE = {
    "id": "e3f1b7a2-5c1d-4f0a-9d7e-2b6a8c4d1f90",
    "name": "Ada Obi",
    "email": "ada@example.com",
    "role": "5bd6924d-b40e-433b-baa4-58a954ac032b",
    "role_name": "Manager",
    "status": "pending",
    "all_locations": False,
    "locations": [_LOCATION_EXAMPLE],
    "expires_at": "2026-11-19T08:15:00Z",
    "last_sent_at": "2026-11-12T08:15:00Z",
    "invited_by_name": "Chidi Okafor",
    "accepted_at": None,
    "revoked_at": None,
    "created_at": "2026-11-12T08:15:00Z",
}

_INVITATION_FIELDS = (
    "**Invitation fields (response)**\n\n"
    "| Field | Type | Meaning |\n"
    "|---|---|---|\n"
    "| `id` | uuid | Invitation id. Use it in the `resend` and `revoke` URLs. |\n"
    "| `name` | string | Invitee's name if the inviter entered one, otherwise an empty string. Fall back to `email` in the UI. |\n"
    "| `email` | string | Invitee's email, lower-case. They must sign in with exactly this address. |\n"
    "| `role` | uuid | Id of the role being offered. |\n"
    "| `role_name` | string | Role name for display, e.g. \"Manager\". |\n"
    "| `status` | `pending`, `accepted`, `revoked`, `expired` | See the status guide below. |\n"
    "| `all_locations` | boolean | `true`: the invitee will work in every location. |\n"
    "| `locations` | array | The locations they will be limited to (empty when `all_locations` is `true`). |\n"
    "| `expires_at` | datetime (UTC) | After this the link stops working. |\n"
    "| `last_sent_at` | datetime (UTC) | When the email was last sent. Resending is blocked for a short cooldown. |\n"
    "| `invited_by_name` | string or null | Who sent it. |\n"
    "| `accepted_at`, `revoked_at` | datetime or null | Set when the invitation reached that state. |\n"
    "| `created_at` | datetime (UTC) | When it was created. |\n\n"
    "Each item of `locations` has:\n\n" + LOCATION_OPTION_FIELDS
)

_STATUS_GUIDE = (
    "**Status guide (what to render)**\n\n"
    "| `status` | Meaning | Buttons to show |\n"
    "|---|---|---|\n"
    "| `pending` | Sent, waiting for the invitee. | Resend, Revoke |\n"
    "| `expired` | Still pending but past `expires_at`. Derived from the date: no date logic needed. | Resend "
    "(issues a fresh link), Revoke |\n"
    "| `accepted` | The invitee joined. Final. | none |\n"
    "| `revoked` | Cancelled by the team. Final. | none (invite again if needed) |\n"
)

_PERMISSION_NOTE = (
    "**Permission:** `INVITE_TEAM_MEMBER` (inviting, resending and the dropdown endpoints). "
    "Revoking needs `REVOKE_INVITATION`."
)

_VIEW_PERMISSION_NOTE = "**Permission:** `VIEW_TEAM`."

_TOKEN_NOTE = (
    "**Token:** the raw token from the email link. It is single-use and only exists in the email. The API "
    "stores a hash, so it can't be recovered: if the link is lost, the team must **resend** the invitation, "
    "which also kills the old link. The token travels in the **request body**, never in the URL."
)


# ===========================================================================
# Team side: /organizations/{organization_id}/invitations/
# ===========================================================================
organization_invitation_schema = extend_schema_view(
    list=extend_schema(
        operation_id="organization_invitations_list",
        tags=TEAM_TAGS,
        summary="List invitations",
        description=(
            "Invitations of this organization, newest first. Use it for the team's *Invitations* table.\n\n"
            + _VIEW_PERMISSION_NOTE + "\n\n" + TEAM_AUTH_NOTE + "\n\n"
            "**Filter:** `?status=` accepts `pending`, `expired`, `accepted` or `revoked`. Any other value "
            "returns `400` listing the accepted ones.\n\n"
            "**Search:** `?search=` matches the invitee's name or email.\n\n"
            "**Ordering:** `?ordering=` accepts `created_at`, `expires_at`, `name`, `email`; prefix with `-` for "
            "descending. Default `-created_at`.\n\n"
            "**Pagination:** the response is paginated: follow the pagination links instead of assuming one page.\n\n"
            + _STATUS_GUIDE + "\n" + _INVITATION_FIELDS + "\n"
            "The token and its hash are never returned."
        ),
        parameters=[
            ORGANIZATION_ID_PARAM,
            OpenApiParameter(
                "status",
                OpenApiTypes.STR,
                OpenApiParameter.QUERY,
                enum=["pending", "expired", "accepted", "revoked"],
                description="Only invitations in this status.",
            ),
        ],
        responses={
            400: VALIDATION_ERROR,
            200: OpenApiResponse(
                response=org_serializers.InvitationSerializer(many=True),
                description="Invitations, newest first.",
                examples=[OpenApiExample("A pending invitation", value=[_INVITATION_EXAMPLE], response_only=True)],
            ),
            401: error_codes(Err.NOT_AUTHENTICATED),
            403: error_codes(Err.PERMISSION_DENIED),
            404: error_codes(Err.NOT_ORGANIZATION_MEMBER),
        },
    ),
    retrieve=extend_schema(
        operation_id="organization_invitations_retrieve",
        tags=TEAM_TAGS,
        summary="Get one invitation",
        description=(
            "A single invitation of this organization. An id from another organization is simply `404`.\n\n"
            + _VIEW_PERMISSION_NOTE + "\n\n" + TEAM_AUTH_NOTE + "\n\n" + _INVITATION_FIELDS
        ),
        parameters=[ORGANIZATION_ID_PARAM],
        responses={
            200: OpenApiResponse(
                response=org_serializers.InvitationSerializer,
                description="The invitation.",
                examples=[OpenApiExample("A pending invitation", value=_INVITATION_EXAMPLE, response_only=True)],
            ),
            401: error_codes(Err.NOT_AUTHENTICATED),
            403: error_codes(Err.PERMISSION_DENIED),
            404: error_codes(Err.NOT_ORGANIZATION_MEMBER, Err.INVITATION_NOT_FOUND),
        },
    ),
    assignable_roles=extend_schema(
        operation_id="organization_invitations_assignable_roles",
        tags=TEAM_TAGS,
        summary="Roles the caller can assign (dropdown)",
        description=(
            "Feeds the **Role** dropdown of the invite form. Call it when the form opens and send the chosen "
            "`id` as `role` when creating the invitation.\n\n"
            + _PERMISSION_NOTE + "\n\n" + TEAM_AUTH_NOTE + "\n\n"
            "**What you get depends on who is calling**\n"
            "- the **Owner**: every role except Owner;\n"
            "- anyone else allowed to invite: every role except Owner and Administrator.\n\n"
            "Role ids are **per organization**: each organization has its own Manager, Accountant, etc. Fetch "
            "from the same `organization_id` you invite into, and refetch when the user switches organization.\n\n"
            "Not paginated.\n\n"
            "| Field | Type | Meaning |\n"
            "|---|---|---|\n"
            "| `id` | uuid | Send as `role`. |\n"
            "| `name` | string | Display name. |\n"
            "| `slug` | string | Stable key, e.g. `sales-representative`. |\n"
            "| `description` | string or null | Help text for the option. |\n"
            "| `is_system` | boolean | `true` for built-in roles. |\n"
        ),
        parameters=[ORGANIZATION_ID_PARAM],
        responses={
            200: org_serializers.RoleOptionSerializer(many=True),
            401: error_codes(Err.NOT_AUTHENTICATED),
            403: error_codes(Err.PERMISSION_DENIED),
            404: error_codes(Err.NOT_ORGANIZATION_MEMBER),
        },
    ),
    assignable_locations=extend_schema(
        operation_id="organization_invitations_assignable_locations",
        tags=TEAM_TAGS,
        summary="Locations the caller can assign (dropdown)",
        description=(
            "Feeds the **Location** picker of the invite form (and of *Change location access* on a member). "
            "Only **active** locations are returned, the default first, then by name.\n\n"
            + _PERMISSION_NOTE + "\n\n" + TEAM_AUTH_NOTE + "\n\n"
            "**What you get depends on who is calling:** someone with access to all locations sees every active "
            "location; someone limited to certain locations only sees those, so they can never hand out "
            "access they don't have.\n\n"
            "**UI tip:** if only one location is returned, hide the picker and send `all_locations: true`.\n\n"
            "Not paginated.\n\n" + LOCATION_OPTION_FIELDS
        ),
        parameters=[ORGANIZATION_ID_PARAM],
        responses={
            200: OpenApiResponse(
                response=org_serializers.LocationOptionSerializer(many=True),
                description="Assignable locations.",
                examples=[
                    OpenApiExample(
                        "Two locations",
                        value=[
                            {
                                "id": "a1b2c3d4-0000-4000-8000-000000000000",
                                "name": "Main Branch",
                                "code": "MAIN",
                                "location_type": "branch",
                                "is_default": True,
                            },
                            _LOCATION_EXAMPLE,
                        ],
                        response_only=True,
                    )
                ],
            ),
            401: error_codes(Err.NOT_AUTHENTICATED),
            403: error_codes(Err.PERMISSION_DENIED),
            404: error_codes(Err.NOT_ORGANIZATION_MEMBER),
        },
    ),
    create=extend_schema(
        operation_id="organization_invitations_create",
        tags=TEAM_TAGS,
        summary="Invite a team member",
        description=(
            "Invites a person by email. The invitee does **not** need an account yet. An email with a one-time "
            "link is sent after the invitation is saved.\n\n"
            + _PERMISSION_NOTE + "\n\n" + TEAM_AUTH_NOTE + "\n\n"
            "**Request body**\n\n"
            "| Field | Required | Meaning |\n"
            "|---|---|---|\n"
            "| `name` | no | Invitee's display name (max 255). Optional: omit it, or send an empty string or null. |\n"
            "| `email` | yes | Trimmed and lower-cased, so `Ada@X.com` and `ada@x.com` are the same person. |\n"
            "| `role` | yes | A role `id` from `GET .../invitations/assignable-roles/` for **this** organization. |\n"
            "| `all_locations`, `locations` | one of them | Where the person can work, see below. |\n\n"
            + LOCATION_SCOPE_FIELDS + "\n" + LOCATION_SCOPE_RULES + "\n\n"
            "**Role rules:** the **Owner** role can never be given by invitation, and only the Owner can "
            "invite an **Administrator**.\n\n"
            "**Business rules (checked in this order)**\n"
            "1. The role and locations must be ones the caller may assign.\n"
            "2. The email must not already belong to a member (`409 ALREADY_MEMBER`, or "
            "`409 MEMBERSHIP_INACTIVE` for a deactivated member: reactivate them instead).\n"
            "3. An **expired** invitation for the same email is retired and a new one issued.\n"
            "4. A still-valid invitation for the same email blocks a new one (`409 ALREADY_INVITED`): use "
            "**resend**.\n"
            "5. The organization needs a free seat (`403 USER_LIMIT_REACHED`).\n\n"
            "**Result:** `201` with the invitation in `pending` status. The token is never returned.\n\n"
            "**Frontend tips:** show `errors.<field>` under the matching input; show `detail` for rule errors "
            "(`403`/`409`) as a banner.\n\n" + ERROR_FORMAT_NOTE
        ),
        parameters=[ORGANIZATION_ID_PARAM],
        request=org_serializers.InvitationCreateSerializer,
        examples=[
            OpenApiExample(
                "Manager limited to one branch",
                request_only=True,
                value={
                    "name": "Ada Obi",
                    "email": "ada@example.com",
                    "role": "5bd6924d-b40e-433b-baa4-58a954ac032b",
                    "all_locations": False,
                    "locations": ["a1b2c3d4-0000-4000-8000-000000000001"],
                },
            ),
            OpenApiExample(
                "Accountant with access everywhere",
                request_only=True,
                value={
                    "name": "Tunde Bello",
                    "email": "tunde@example.com",
                    "role": "9a1f0d52-3b7c-4e5d-8a1e-6f2c4b7d9e03",
                    "all_locations": True,
                },
            ),
        ],
        responses={
            201: OpenApiResponse(
                response=org_serializers.InvitationSerializer,
                description="Invitation created and emailed.",
                examples=[OpenApiExample("Created", value=_INVITATION_EXAMPLE, response_only=True)],
            ),
            400: VALIDATION_ERROR,
            401: error_codes(Err.NOT_AUTHENTICATED),
            403: error_codes(
                Err.PERMISSION_DENIED,
                Err.ROLE_NOT_ASSIGNABLE,
                Err.LOCATION_SCOPE_NOT_ALLOWED,
                Err.USER_LIMIT_REACHED,
            ),
            404: error_codes(Err.NOT_ORGANIZATION_MEMBER),
            409: error_codes(Err.ALREADY_MEMBER, Err.MEMBERSHIP_INACTIVE, Err.ALREADY_INVITED),
        },
    ),
    resend=extend_schema(
        operation_id="organization_invitations_resend",
        tags=TEAM_TAGS,
        summary="Resend an invitation",
        description=(
            "Issues a **fresh link** for a pending invitation and extends its expiry. The previous link stops "
            "working immediately. Use it when the invitee lost the email or the invitation expired. The location "
            "scope and role stay as originally set.\n\n"
            + _PERMISSION_NOTE + "\n\n" + TEAM_AUTH_NOTE + "\n\n"
            "**Body:** none.\n\n"
            "**Rules**\n"
            "- Only `pending` and `expired` invitations can be resent (`409 INVITATION_NOT_PENDING` for "
            "`accepted` or `revoked`: create a new invitation).\n"
            "- Resending twice in a short time returns `429 INVITATION_RESEND_TOO_SOON` with the wait in "
            "`Retry-After`. Disable the button for that long instead of retrying.\n"
            "- Administrator invitations can only be resent by the Owner (`403 ROLE_NOT_ASSIGNABLE`).\n"
            "- Reviving an **expired** invitation needs a free seat (`403 USER_LIMIT_REACHED`).\n\n"
            "**Result:** `200` with the updated invitation (new `last_sent_at` and `expires_at`)."
        ),
        parameters=[ORGANIZATION_ID_PARAM],
        request=None,
        responses={
            200: org_serializers.InvitationSerializer,
            401: error_codes(Err.NOT_AUTHENTICATED),
            403: error_codes(Err.PERMISSION_DENIED, Err.ROLE_NOT_ASSIGNABLE, Err.USER_LIMIT_REACHED),
            404: error_codes(Err.NOT_ORGANIZATION_MEMBER, Err.INVITATION_NOT_FOUND),
            409: error_codes(Err.INVITATION_NOT_PENDING),
            429: error_codes(Err.INVITATION_RESEND_TOO_SOON),
        },
    ),
    revoke=extend_schema(
        operation_id="organization_invitations_revoke",
        tags=TEAM_TAGS,
        summary="Revoke an invitation",
        description=(
            "Cancels a pending invitation. The emailed link stops working at once. The record is **kept** for "
            "history and shows as `revoked`: nothing is deleted.\n\n"
            "**Permission:** `REVOKE_INVITATION` (separate from the permission to invite).\n\n"
            + TEAM_AUTH_NOTE + "\n\n"
            "**Body (optional)**\n\n"
            "| Field | Meaning |\n"
            "|---|---|\n"
            "| `reason` | Up to 500 characters, may be empty. Stored in the audit log only; the invitee never "
            "sees it. |\n\n"
            "**Rules**\n"
            "- Only `pending` and `expired` invitations can be revoked (`409` otherwise).\n"
            "- Administrator invitations can only be revoked by the Owner (`403 ROLE_NOT_ASSIGNABLE`).\n"
            "- After revoking, the same email can be invited again.\n\n"
            "**Result:** `200` with the invitation in `revoked` status."
        ),
        parameters=[ORGANIZATION_ID_PARAM],
        request=org_serializers.InvitationRevokeSerializer,
        examples=[
            OpenApiExample("With a reason", value={"reason": "Invited with the wrong email."}, request_only=True),
        ],
        responses={
            200: org_serializers.InvitationSerializer,
            400: VALIDATION_ERROR,
            401: error_codes(Err.NOT_AUTHENTICATED),
            403: error_codes(Err.PERMISSION_DENIED, Err.ROLE_NOT_ASSIGNABLE),
            404: error_codes(Err.NOT_ORGANIZATION_MEMBER, Err.INVITATION_NOT_FOUND),
            409: error_codes(Err.INVITATION_NOT_PENDING),
        },
    ),
)


# ===========================================================================
# Public side: /invitations/validate/ and /invitations/accept/
# ===========================================================================
invitation_schema = extend_schema_view(
    validate=extend_schema(
        operation_id="invitations_validate",
        tags=PUBLIC_TAGS,
        summary="Preview an invitation",
        description=(
            "**Public: no login needed.** Call it when the invite page opens, to show *who invited you, to which "
            "organization, in what role and where* before the invitee signs up or logs in. It changes nothing: "
            "the token stays valid.\n\n"
            + _TOKEN_NOTE + "\n\n"
            "**Request body:** `{\"token\": \"...\"}`.\n\n"
            "**Response fields**\n\n"
            "| Field | Type | Meaning |\n"
            "|---|---|---|\n"
            "| `organization` | string | Organization name. |\n"
            "| `role` | string | Role name. |\n"
            "| `name` | string | Name the inviter entered, or an empty string. If empty, greet the user without a name. |\n"
            "| `invited_email` | string | **Masked**, e.g. `a***@example.com`. Ask the user to sign up or log in "
            "with the address the invitation was sent to. |\n"
            "| `account_exists` | boolean | `true`: that email already has an account, so send the user to "
            "**log in**. `false`: send them to **sign up** with that email (and then verify it). |\n"
            "| `all_locations` | boolean | `true`: access to every location. |\n"
            "| `locations` | array of strings | Location **names** (not ids) they were invited to. Empty when "
            "`all_locations` is `true`. |\n"
            "| `expires_at` | datetime (UTC) | Show it in the user's local time. |\n\n"
            "Nothing else about the organization or its members is exposed.\n\n"
            "**Not found vs expired**\n"
            "- `404 INVITATION_INVALID`: unknown, already used or revoked token, or the organization is "
            "deactivated. The API deliberately doesn't say which: keep the message generic.\n"
            "- `410 INVITATION_EXPIRED`: a real invitation past `expires_at`. Tell the user to ask for a new "
            "one; the team can resend it.\n\n"
            "**Rate limit:** limited per client IP. On `429`, wait for `Retry-After`; never retry "
            "in a loop.\n\n" + ERROR_FORMAT_NOTE
        ),
        auth=[],
        request=org_serializers.AcceptInvitationSerializer,
        parameters=[RETRY_AFTER],
        examples=[OpenApiExample("Token from the email link", request_only=True, value={"token": "<token-from-email>"})],
        responses={
            200: OpenApiResponse(
                response=org_serializers.InvitationPreviewSerializer,
                description="The invitation is valid and can still be accepted.",
                examples=[
                    OpenApiExample(
                        "Valid invitation",
                        value={
                            "organization": "Okafor & Sons Stores",
                            "role": "Manager",
                            "name": "Ada Obi",
                            "invited_email": "a**@example.com",
                            "account_exists": True,
                            "all_locations": False,
                            "locations": ["Lekki Branch"],
                            "expires_at": "2026-11-19T08:15:00Z",
                        },
                        response_only=True,
                    )
                ],
            ),
            400: VALIDATION_ERROR,
            404: coded_error(
                "Unknown, used or revoked token, or deactivated organization.",
                ("Invalid", Err.INVITATION_INVALID, "This invitation link is not valid."),
            ),
            410: coded_error(
                "The invitation has expired.",
                ("Expired", Err.INVITATION_EXPIRED, "This invitation has expired."),
            ),
            429: TOO_MANY_REQUESTS,
        },
    ),
    accept=extend_schema(
        operation_id="invitations_accept",
        tags=PUBLIC_TAGS,
        summary="Accept an invitation",
        description=(
            "Turns the invitation into a membership, with the role and location access the inviter chose. This is "
            "the step that consumes the token.\n\n"
            "**Auth:** the caller must be **signed in** (`401` otherwise), with an email that is **verified** and "
            "**equal to the invited email**.\n\n"
            + _TOKEN_NOTE + "\n\n"
            "**Workflow**\n"
            "1. Open the link and call **validate** to render the invite screen.\n"
            "2. If the user isn't signed in, send them to sign up or log in **with the invited email**, then to "
            "email verification if needed. Keep the token across the redirects (memory or `sessionStorage`).\n"
            "3. Call this endpoint with the token.\n"
            "4. On `200`, switch to the returned organization.\n\n"
            "**Request body:** `{\"token\": \"...\"}`.\n\n"
            "**Response fields**\n\n"
            "| Field | Type | Meaning |\n"
            "|---|---|---|\n"
            "| `membership_id` | uuid | The new membership. |\n"
            "| `organization_id` | uuid | Switch the app to this organization. |\n"
            "| `organization_name` | string | For the welcome message. |\n"
            "| `role` | string | Role name. |\n"
            "| `all_locations` | boolean | `true`: access to every location. |\n"
            "| `locations` | array | Locations they can use (objects, see the invite preview). Empty when "
            "`all_locations` is `true`. |\n\n"
            "Replaying a successful accept with the same user is safe and returns the same membership.\n\n"
            "**What to do for each error**\n"
            "- `401`: not signed in. Send them to login and come back.\n"
            "- `403 EMAIL_NOT_VERIFIED`: send them to email verification, then back here.\n"
            "- `403 INVITATION_EMAIL_MISMATCH`: wrong account. Say which account is active and offer *Log out "
            "and switch account*. Never offer to accept anyway.\n"
            "- `403 USER_LIMIT_REACHED`: no free seat. The invitee can't fix this: tell them to contact an admin.\n"
            "- `404 INVITATION_INVALID` / `410 INVITATION_EXPIRED`: same handling as in validate.\n"
            "- `409 INVITATION_LOCATIONS_UNAVAILABLE`: every location in the invitation has been deactivated "
            "since it was sent. Ask the team to send a new invitation.\n"
            "- `409 ALREADY_MEMBER`: not a failure for the user: route them into the organization.\n"
            "- `409 MEMBERSHIP_INACTIVE`: their access was deactivated earlier. Tell them to contact an admin.\n\n"
            "**Double clicks:** disable the button while the request runs.\n\n" + ERROR_FORMAT_NOTE
        ),
        request=org_serializers.AcceptInvitationSerializer,
        parameters=[RETRY_AFTER],
        examples=[OpenApiExample("Accept", request_only=True, value={"token": "<token-from-email>"})],
        responses={
            200: OpenApiResponse(
                response=org_serializers.InvitationAcceptedSerializer,
                description="Accepted. The membership is active.",
                examples=[
                    OpenApiExample(
                        "Accepted",
                        value={
                            "membership_id": "6b1d2a77-4c1f-4d0e-9a0b-7c3f2b5e8d21",
                            "organization_id": "0c9d3f1e-6f6b-4a53-8e5a-0e2a9a3a1b10",
                            "organization_name": "Okafor & Sons Stores",
                            "role": "Manager",
                            "all_locations": False,
                            "locations": [_LOCATION_EXAMPLE],
                        },
                        response_only=True,
                    )
                ],
            ),
            400: VALIDATION_ERROR,
            401: error_codes(Err.NOT_AUTHENTICATED),
            403: error_codes(Err.EMAIL_NOT_VERIFIED, Err.INVITATION_EMAIL_MISMATCH, Err.USER_LIMIT_REACHED),
            404: error_codes(Err.INVITATION_INVALID),
            409: error_codes(Err.ALREADY_MEMBER, Err.MEMBERSHIP_INACTIVE, Err.INVITATION_LOCATIONS_UNAVAILABLE),
            410: error_codes(Err.INVITATION_EXPIRED),
            429: TOO_MANY_REQUESTS,
        },
    ),
)
