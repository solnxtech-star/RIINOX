
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample
from drf_spectacular.utils import OpenApiParameter
from drf_spectacular.utils import OpenApiResponse
from drf_spectacular.utils import extend_schema
from drf_spectacular.utils import extend_schema_view

from core.applications.users.api.schemas.common import FORBIDDEN_MANAGER
from core.applications.users.api.schemas.common import LOCATION_OPTION_FIELDS
from core.applications.users.api.schemas.common import LOCATION_SCOPE_FIELDS
from core.applications.users.api.schemas.common import NOT_FOUND
from core.applications.users.api.schemas.common import UNAUTHENTICATED
from core.applications.users.api.schemas.common import VALIDATION_ERROR
from core.applications.users.api.schemas.common import error_codes
from core.applications.users.api.schemas.common import error_examples
from core.applications.users.api.serializers import (
    organization_serializers as org_serializers,
)
from core.applications.users.errors import InvitationErrorCode as Err

ORGANIZATION_FILTER = OpenApiParameter(
    "organization",
    OpenApiTypes.STR,
    OpenApiParameter.QUERY,
    description="Only memberships of this organization (its id).",
)

_LOCATION_ACCESS_NOTE = (
    "**Location access:** `all_locations` is `true` when the member can work in every location; otherwise "
    "`locations` lists the ones they can use, as objects:\n\n" + LOCATION_OPTION_FIELDS
)

_MEMBERSHIP_RULES_400 = error_examples(
    "A membership rule was violated.",
    ("Owner is protected", {"non_field_errors": ["The Owner's membership cannot be changed here."]}),
    ("Own membership", {"non_field_errors": ["You cannot change your own membership."]}),
    ("Owner role not assignable", {"role": ["The Owner role cannot be assigned to another member."]}),
    ("Administrator role", {"role": ["Only the Owner can grant the Administrator role."]}),
)


def _update_membership(summary: str):
    return extend_schema(
        summary=summary,
        description=(
            "Changes a member's **role** and/or **active** flag. **Administrators and Owners** only. To change "
            "which locations the member can use, call `PUT /memberships/{id}/locations/`.\n\n"
            "**Rules**\n"
            "- The Owner's membership can't be changed.\n"
            "- Only the Owner can change an Administrator or grant the Administrator role.\n"
            "- The Owner role is never assignable.\n"
            "- Nobody can change their own membership.\n"
            "- A role must belong to the membership's organization.\n\n"
            "Setting `active` to `false` removes the member's access immediately; their history is kept."
        ),
        request=org_serializers.MembershipUpdateSerializer,
        responses={
            200: OpenApiResponse(response=org_serializers.MembershipSerializer, description="The updated membership."),
            400: _MEMBERSHIP_RULES_400,
            401: UNAUTHENTICATED,
            403: FORBIDDEN_MANAGER,
            404: NOT_FOUND,
        },
    )


membership_schema = extend_schema_view(
    list=extend_schema(
        summary="List memberships",
        description=(
            "Memberships the caller can see: **their own**, plus **every membership of the organizations they "
            "manage** (Owner or Administrator). Regular members therefore only see themselves. Filter with "
            "`?organization=`.\n\n"
            "Use it to find the signed-in user's role and location access in each organization.\n\n"
            + _LOCATION_ACCESS_NOTE
        ),
        parameters=[ORGANIZATION_FILTER],
        responses={
            200: OpenApiResponse(
                response=org_serializers.MembershipSerializer(many=True),
                description="Visible memberships.",
            ),
            400: error_examples(
                "Malformed `organization` filter.",
                ("Bad filter", {"organization": "Invalid organization id."}),
            ),
            401: UNAUTHENTICATED,
        },
    ),
    retrieve=extend_schema(
        summary="Retrieve a membership",
        description=(
            "One membership: user, organization, role, active status and location access. Only visible if it is "
            "the caller's own or belongs to an organization they manage.\n\n" + _LOCATION_ACCESS_NOTE
        ),
        responses={
            200: OpenApiResponse(response=org_serializers.MembershipSerializer, description="The membership."),
            401: UNAUTHENTICATED,
            404: NOT_FOUND,
        },
    ),
    create=extend_schema(exclude=True),
    update=_update_membership("Update a membership"),
    partial_update=_update_membership("Partially update a membership"),
    destroy=extend_schema(
        summary="Remove a member",
        description=(
            "**Administrators and Owners** only. The member is **deactivated, not deleted**, so history and "
            "audit references stay intact; they lose access to the organization immediately. The same "
            "protections as updating apply (Owner, own membership, Administrators).\n\n"
            "To cancel someone who hasn't joined yet, revoke their invitation instead: "
            "`POST /organizations/{id}/invitations/{invitation_id}/revoke/`."
        ),
        request=None,
        responses={
            204: OpenApiResponse(description="Removed."),
            400: _MEMBERSHIP_RULES_400,
            401: UNAUTHENTICATED,
            403: FORBIDDEN_MANAGER,
            404: NOT_FOUND,
        },
    ),
    set_locations=extend_schema(
        summary="Change a member's location access",
        description=(
            "Replaces the locations a member can work in. **Administrators and Owners** only. Send the full "
            "new scope: it replaces the old one, it is not merged.\n\n"
            "**Request body**\n\n" + LOCATION_SCOPE_FIELDS + "\n"
            "**Rules**\n"
            "- Send **exactly one** of: `all_locations: true`, or a non-empty `locations` list.\n"
            "- Get the choices from `GET /organizations/{organization_id}/invitations/assignable-locations/`.\n"
            "- Administrators always keep all locations.\n"
            "- You can't change your own access, and someone limited to certain locations can only hand out those.\n"
            "- The change is recorded in the audit log with the previous and new scope.\n\n"
            "Returns the updated membership."
        ),
        request=org_serializers.MembershipLocationsSerializer,
        examples=[
            OpenApiExample("All locations", request_only=True, value={"all_locations": True}),
            OpenApiExample(
                "Two specific locations",
                request_only=True,
                value={
                    "all_locations": False,
                    "locations": [
                        "a1b2c3d4-0000-4000-8000-000000000001",
                        "a1b2c3d4-0000-4000-8000-000000000002",
                    ],
                },
            ),
        ],
        responses={
            200: OpenApiResponse(response=org_serializers.MembershipSerializer, description="The updated membership."),
            400: VALIDATION_ERROR,
            401: UNAUTHENTICATED,
            403: error_codes(Err.PERMISSION_DENIED, Err.LOCATION_SCOPE_NOT_ALLOWED, Err.ROLE_NOT_ASSIGNABLE),
            404: NOT_FOUND,
        },
    ),
)
