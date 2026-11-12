from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample
from drf_spectacular.utils import OpenApiParameter
from drf_spectacular.utils import OpenApiResponse
from drf_spectacular.utils import inline_serializer
from rest_framework import serializers

from core.applications.users.api.serializers import (
    organization_serializers as org_serializers,
)
from core.applications.users.errors import InvitationErrorCode as Err

# ===========================================================================
# Response helpers
# ===========================================================================
DetailSerializer = inline_serializer(
    name="Detail",
    fields={"detail": serializers.CharField()},
)


def error_examples(description: str, *examples: tuple[str, dict]) -> OpenApiResponse:
    """A documented error response. Each example is a (name, response body) pair."""
    return OpenApiResponse(
        description=description,
        examples=[OpenApiExample(name, value=value, response_only=True) for name, value in examples],
    )


def coded_error(description: str, *examples: tuple[str, Err, str]) -> OpenApiResponse:
    """A business-rule error: `{"code": "...", "detail": "..."}`, one example per (name, code, detail)."""
    return OpenApiResponse(
        org_serializers.ErrorSerializer,
        description=description,
        examples=[
            OpenApiExample(name, value={"code": code.value, "detail": detail}, response_only=True)
            for name, code, detail in examples
        ],
    )


def error_codes(*codes: Err) -> OpenApiResponse:
    """
    Lists the codes an endpoint can return. Built from the real enum, so a
    renamed code breaks the docs build instead of leaving stale documentation.
    """
    return OpenApiResponse(
        org_serializers.ErrorSerializer,
        description="Possible `code` values: " + ", ".join(f"`{code.value}`" for code in codes) + ".",
    )


UNAUTHENTICATED = OpenApiResponse(description="Authentication credentials were not provided or are invalid.")
NOT_FOUND = OpenApiResponse(
    description="Not found. Also returned when the resource belongs to an organization the caller isn't a member of."
)
FORBIDDEN_MANAGER = error_examples(
    "The caller is a member but not an Administrator or Owner of this organization.",
    ("Not a manager", {"detail": "Only organization Administrators and Owners can perform this action."}),
)
FORBIDDEN_OWNER = error_examples(
    "The caller is a member but not the Owner of this organization.",
    ("Not the Owner", {"detail": "Only the organization Owner can perform this action."}),
)
VALIDATION_ERROR = OpenApiResponse(
    org_serializers.ValidationErrorSerializer,
    description=(
        "A field failed validation. `errors` maps each field name to its list of messages: "
        "show them under the matching input."
    ),
    examples=[
        OpenApiExample(
            "Validation error",
            value={
                "code": "VALIDATION_ERROR",
                "detail": "Invalid input.",
                "errors": {"email": ["Enter a valid email address."]},
            },
            response_only=True,
        )
    ],
)

# ===========================================================================
# Parameters
# ===========================================================================
ORGANIZATION_ID_PARAM = OpenApiParameter(
    name="organization_id",
    type=OpenApiTypes.UUID,
    location=OpenApiParameter.PATH,
    description=(
        "The organization being managed. The caller must be an active member of it. "
        "It always comes from the URL and is never read from the request body."
    ),
)

# ===========================================================================
# Rate limiting and caching
# ===========================================================================
RETRY_AFTER = OpenApiParameter(
    "Retry-After",
    OpenApiTypes.INT,
    OpenApiParameter.HEADER,
    response=[429],
    description="Seconds to wait before trying again.",
)

TOO_MANY_REQUESTS = OpenApiResponse(
    description=(
        "Too many requests. Wait for the number of seconds in the `Retry-After` header, then try again. "
        "Disable the button meanwhile and never retry in a loop."
    ),
)


def cache_header(example: str) -> OpenApiParameter:
    """Documents the `Cache-Control` header of a cacheable response, e.g. `private, max-age=3600`."""
    return OpenApiParameter(
        "Cache-Control",
        OpenApiTypes.STR,
        OpenApiParameter.HEADER,
        response=[200],
        description=f"Browsers may cache this response (`{example}`).",
    )


# ===========================================================================
# Reusable documentation blocks (markdown, rendered by Swagger/Redoc)
# ===========================================================================
ERROR_FORMAT_NOTE = (
    "**Errors:** business-rule errors look like `{\"code\": \"...\", \"detail\": \"...\"}`: branch on "
    "`code`, never on `detail` (the wording may change). Field errors (`400`) look like "
    "`{\"code\": \"VALIDATION_ERROR\", \"detail\": \"Invalid input.\", \"errors\": {\"<field>\": [\"...\"]}}`."
)

TEAM_AUTH_NOTE = (
    "**Auth:** needs a valid access token (`401` otherwise). The organization in the URL is checked against "
    "the caller's active membership: a non-member gets `404` (not `403`), so the existence of other "
    "organizations is never revealed. A member who lacks the permission listed below gets `403`."
)

LOCATION_OPTION_FIELDS = (
    "| Field | Type | Meaning |\n"
    "|---|---|---|\n"
    "| `id` | uuid | Location id. Send this in `locations`. |\n"
    "| `name` | string | Display name, e.g. \"Main Branch\". |\n"
    "| `code` | string | Short code unique within the organization, e.g. \"MAIN\". |\n"
    "| `location_type` | `branch`, `store`, `warehouse`, `office`, `other` | Use it for an icon or label. |\n"
    "| `is_default` | boolean | `true` for the organization's default location (listed first). |\n"
)

LOCATION_SCOPE_FIELDS = (
    "| Field | Type | Meaning |\n"
    "|---|---|---|\n"
    "| `all_locations` | boolean, default `false` | `true`: the person can work in every location, including ones "
    "added later. |\n"
    "| `locations` | array of location ids | Only these locations. Required when `all_locations` is `false`; "
    "must be empty or omitted when it is `true`. |\n"
)

LOCATION_SCOPE_RULES = (
    "**Location rules**\n"
    "- Send **exactly one** of: `all_locations: true`, or a non-empty `locations` list.\n"
    "- Take location ids from `GET /organizations/{organization_id}/invitations/assignable-locations/`. "
    "An id from another organization is rejected as if it did not exist.\n"
    "- **Administrators always have all locations**: sending specific locations for an Administrator role is rejected.\n"
    "- Someone limited to certain locations can only hand out those locations, and can never grant "
    "`all_locations`.\n"
    "- A single-location organization can send `all_locations: true` and never show a picker."
)
