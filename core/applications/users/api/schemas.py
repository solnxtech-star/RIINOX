from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample
from drf_spectacular.utils import OpenApiParameter
from drf_spectacular.utils import OpenApiResponse
from drf_spectacular.utils import extend_schema
from drf_spectacular.utils import inline_serializer
from rest_framework import serializers

from core.applications.users.api.serializers.organization_serializers import (
    AcceptInvitationSerializer,
)
from core.applications.users.api.serializers.organization_serializers import (
    BusinessTypeSerializer,
)
from core.applications.users.api.serializers.organization_serializers import (
    InvitationCreateSerializer,
)
from core.applications.users.api.serializers.organization_serializers import (
    InvitationPreviewSerializer,
)
from core.applications.users.api.serializers.organization_serializers import (
    MembershipSerializer,
)
from core.applications.users.api.serializers.organization_serializers import (
    MembershipUpdateSerializer,
)
from core.applications.users.api.serializers.organization_serializers import (
    OrganizationBusinessTypesUpdateSerializer,
)
from core.applications.users.api.serializers.organization_serializers import (
    OrganizationCreateSerializer,
)
from core.applications.users.api.serializers.organization_serializers import (
    OrganizationMemberSerializer,
)
from core.applications.users.api.serializers.organization_serializers import (
    OrganizationSerializer,
)
from core.applications.users.api.serializers.organization_serializers import (
    OrganizationUpdateSerializer,
)
from core.applications.users.api.serializers.organization_serializers import (
    RoleSerializer,
)
from core.applications.users.models import Membership

# ===========================================================================
# Shared building blocks
# ===========================================================================
DetailSerializer = inline_serializer(
    name="Detail",
    fields={"detail": serializers.CharField()},
)

InvitationSentSerializer = inline_serializer(
    name="InvitationSent",
    fields={
        "detail": serializers.CharField(),
        "id": serializers.ModelField(model_field=Membership._meta.pk, read_only=True),
    },
)

UNAUTHENTICATED = OpenApiResponse(description="Authentication credentials were not provided or are invalid.")
NOT_FOUND = OpenApiResponse(
    description="Not found. Also returned when the resource belongs to an organization the caller isn't a member of."
)
FORBIDDEN_MANAGER = OpenApiResponse(
    description="The caller is a member but not an Administrator or Owner of this organization.",
    examples=[
        OpenApiExample(
            "Not a manager",
            value={"detail": "Only organization Administrators and Owners can perform this action."},
            response_only=True,
        )
    ],
)
FORBIDDEN_OWNER = OpenApiResponse(
    description="The caller is a member but not the Owner of this organization.",
    examples=[
        OpenApiExample(
            "Not the Owner",
            value={"detail": "Only the organization Owner can perform this action."},
            response_only=True,
        )
    ],
)

ORGANIZATION_ID = OpenApiParameter(
    "organization",
    OpenApiTypes.STR,
    OpenApiParameter.QUERY,
    description="Only memberships of this organization.",
)


# ===========================================================================
# Business types
# ===========================================================================
list_business_types_schema = extend_schema(
    summary="List business types",
    description=(
        "The catalog of business types, ordered for display. Use `?context=onboarding` to get only the "
        "cards shown on the onboarding screen; without it you get every active type (for the later "
        "\"edit business types\" screen). `code` is the stable key the other endpoints use."
    ),
    parameters=[
        OpenApiParameter(
            "context",
            OpenApiTypes.STR,
            OpenApiParameter.QUERY,
            enum=["onboarding"],
            description="`onboarding` returns only the cards shown on the onboarding screen.",
        )
    ],
    responses={200: OpenApiResponse(response=BusinessTypeSerializer(many=True), description="Business types."), 401: UNAUTHENTICATED},
)

retrieve_business_type_schema = extend_schema(
    summary="Retrieve a business type",
    description="One business type by its `code` (for example `wholesale_distribution`).",
    responses={200: OpenApiResponse(response=BusinessTypeSerializer, description="The business type."), 401: UNAUTHENTICATED, 404: NOT_FOUND},
)


# ===========================================================================
# Organizations
# ===========================================================================
list_organization_schema = extend_schema(
    summary="List my organizations",
    description=(
        "Organizations the authenticated user is an **accepted, active member** of. "
        "Deactivated organizations and memberships are excluded. Each entry includes the plan "
        "and the organization's business types (primary first). Members are not embedded: "
        "use `GET /organizations/{id}/members/`."
    ),
    responses={
        200: OpenApiResponse(response=OrganizationSerializer(many=True), description="Organizations the user belongs to."),
        401: UNAUTHENTICATED,
    },
)

create_organization_schema = extend_schema(
    summary="Create an organization (onboarding)",
    description=(
        "Creates a tenant from the two onboarding screens: the chosen **business type** and the "
        "**business details**, submitted together so no half-created organization can exist.\n\n"
        "What happens in one transaction:\n"
        "- the caller becomes the organization's **Owner**;\n"
        "- the chosen type becomes the **primary business type** (add more later with "
        "`PUT /organizations/{id}/business-types/`);\n"
        "- the built-in roles, the **Free plan** subscription, document numbering sequences and the "
        "shared default invoice/receipt templates are set up.\n\n"
        "Business type codes come from `GET /business-types/?context=onboarding`. `phone` is normalised "
        "to international format (`0803 123 4567` becomes `+2348031234567`) and `state` must be a valid "
        "Nigerian state. Branding and templates are configured later with `PATCH /organizations/{id}/`."
    ),
    request=OrganizationCreateSerializer,
    examples=[
        OpenApiExample(
            "Retail shop",
            request_only=True,
            value={
                "business_type": "retail",
                "name": "Okafor & Sons Stores",
                "registration_number": "RC1234567",
                "state": "Rivers",
                "address": "12 Aba Road, Port Harcourt",
                "phone": "0803 123 4567",
                "staff_size": "6-10",
            },
        )
    ],
    responses={
        201: OpenApiResponse(response=OrganizationSerializer, description="Organization created; the caller is its Owner."),
        400: OpenApiResponse(
            description="Validation error.",
            examples=[
                OpenApiExample(
                    "Invalid input",
                    value={
                        "business_type": ["Object with code=nope does not exist."],
                        "state": ["Select a valid state."],
                        "phone": ["Enter a valid phone number."],
                    },
                    response_only=True,
                )
            ],
        ),
        401: UNAUTHENTICATED,
    },
)

retrieve_organization_schema = extend_schema(
    summary="Retrieve an organization",
    description=(
        "A single organization with its plan and business types. Available to every active member "
        "of the organization. Members and roles have their own endpoints."
    ),
    responses={
        200: OpenApiResponse(response=OrganizationSerializer, description="The organization."),
        401: UNAUTHENTICATED,
        404: NOT_FOUND,
    },
)


def _update_organization_schema(summary: str):
    return extend_schema(
        summary=summary,
        description=(
            "Updates the organization's profile and branding: name, contact details, CAC/RC number, "
            "timezone, logo, header/footer and document templates. **Administrators and Owners** only.\n\n"
            "- `country` and `currency` are fixed after creation, and business types have their own "
            "endpoint.\n"
            "- A document template must be a shared default or one owned by this organization; "
            "**custom templates** additionally require a plan that includes them.\n\n"
            "Returns the full organization."
        ),
        request=OrganizationUpdateSerializer,
        responses={
            200: OpenApiResponse(response=OrganizationSerializer, description="The updated organization."),
            400: OpenApiResponse(
                description="Validation error.",
                examples=[
                    OpenApiExample(
                        "Custom template not on plan",
                        value={"invoice_template": ["Custom templates are not available on your current plan."]},
                        response_only=True,
                    )
                ],
            ),
            401: UNAUTHENTICATED,
            403: FORBIDDEN_MANAGER,
            404: NOT_FOUND,
        },
    )


update_organization_schema = _update_organization_schema("Update an organization")
partial_update_organization_schema = _update_organization_schema("Partially update an organization")

deactivate_organization_schema = extend_schema(
    summary="Deactivate an organization",
    description=(
        "Soft-deactivates the organization. **Owner** only. Organizations are never hard-deleted "
        "(`DELETE /organizations/{id}/` returns 405): deactivation keeps every business record, but the "
        "organization disappears from all members' lists and the API. Reactivation is a support action. "
        "Calling it again returns 404, as the organization is no longer visible."
    ),
    request=None,
    responses={
        200: OpenApiResponse(
            response=DetailSerializer,
            description="Deactivated.",
            examples=[
                OpenApiExample(
                    "Deactivated",
                    value={"detail": "Organization 'Okafor & Sons Stores' deactivated."},
                    response_only=True,
                )
            ],
        ),
        401: UNAUTHENTICATED,
        403: FORBIDDEN_OWNER,
        404: NOT_FOUND,
    },
)

business_types_organization_schema = extend_schema(
    summary="Replace an organization's business types",
    description=(
        "Replaces the **full set** of business types: send every type the organization should have "
        "and mark exactly one as primary. The primary type drives terminology and dashboard defaults. "
        "Idempotent: sending the same body twice changes nothing. **Administrators and Owners** only.\n\n"
        "Codes come from `GET /business-types/`."
    ),
    request=OrganizationBusinessTypesUpdateSerializer,
    examples=[
        OpenApiExample(
            "Retail and wholesale",
            request_only=True,
            value={"business_types": ["retail", "wholesale_distribution"], "primary_business_type": "retail"},
        )
    ],
    responses={
        200: OpenApiResponse(response=OrganizationSerializer, description="The organization with its new business types."),
        400: OpenApiResponse(
            description="Empty selection, duplicates, unknown code, or a primary type that isn't in the selection.",
            examples=[
                OpenApiExample(
                    "Primary not selected",
                    value={"primary_business_type": ["The primary business type must be one of the selected types."]},
                    response_only=True,
                )
            ],
        ),
        401: UNAUTHENTICATED,
        403: FORBIDDEN_MANAGER,
        404: NOT_FOUND,
    },
)

member_organization_schema = extend_schema(
    summary="List organization members",
    description=(
        "Everyone in the organization, **including pending invitations** (`accepted: false`, with the "
        "invited email in `user`). Ordered by when they joined or were invited. Paginated when your "
        "project paginates by default. **Administrators and Owners** only."
    ),
    responses={
        200: OpenApiResponse(
            response=OrganizationMemberSerializer(many=True),
            description="Members and pending invitations.",
            examples=[
                OpenApiExample(
                    "Owner, an administrator and a pending invitation",
                    value=[
                        {"id": 1, "user": "owner@techify.com", "role": "Owner", "active": True, "accepted": True,
                         "joined_at": "2026-09-01T09:00:00Z"},
                        {"id": 2, "user": "admin@techify.com", "role": "Administrator", "active": True, "accepted": True,
                         "joined_at": "2026-09-02T10:30:00Z"},
                        {"id": 3, "user": "new.hire@techify.com", "role": "Front Desk", "active": True, "accepted": False,
                         "joined_at": "2026-09-05T08:15:00Z"},
                    ],
                    response_only=True,
                )
            ],
        ),
        401: UNAUTHENTICATED,
        403: FORBIDDEN_MANAGER,
        404: NOT_FOUND,
    },
)

roles_organization_schema = extend_schema(
    summary="List assignable roles",
    description=(
        "Roles defined for this organization (built-in and custom); feeds the role picker when inviting "
        "or changing a member. The Owner role is listed but can never be assigned to another member. "
        "**Administrators and Owners** only."
    ),
    responses={
        200: OpenApiResponse(response=RoleSerializer(many=True), description="The organization's roles."),
        401: UNAUTHENTICATED,
        403: FORBIDDEN_MANAGER,
        404: NOT_FOUND,
    },
)


# ===========================================================================
# Memberships
# ===========================================================================
list_members_schema = extend_schema(
    summary="List memberships",
    description=(
        "Memberships the caller can see: **their own**, plus **every membership of the organizations they "
        "manage** (Owner or Administrator). Regular members therefore see only themselves. "
        "Filter with `?organization=`."
    ),
    parameters=[ORGANIZATION_ID],
    responses={
        200: OpenApiResponse(response=MembershipSerializer(many=True), description="Visible memberships."),
        400: OpenApiResponse(
            description="Malformed `organization` filter.",
            examples=[OpenApiExample("Bad filter", value={"organization": "Invalid organization id."}, response_only=True)],
        ),
        401: UNAUTHENTICATED,
    },
)

retrieve_member_schema = extend_schema(
    summary="Retrieve a membership",
    description=(
        "One membership: user, organization, role, active/accepted status and (for pending invitations) "
        "the expiry. Only visible if it is the caller's own or belongs to an organization they manage."
    ),
    responses={
        200: OpenApiResponse(response=MembershipSerializer, description="The membership."),
        401: UNAUTHENTICATED,
        404: NOT_FOUND,
    },
)

_MEMBERSHIP_RULES_400 = OpenApiResponse(
    description="A membership rule was violated.",
    examples=[
        OpenApiExample("Owner is protected", value={"non_field_errors": ["The Owner's membership cannot be changed here."]}, response_only=True),
        OpenApiExample("Own membership", value={"non_field_errors": ["You cannot change your own membership."]}, response_only=True),
        OpenApiExample("Owner role not assignable", value={"role": ["The Owner role cannot be assigned to another member."]}, response_only=True),
        OpenApiExample("Administrator role", value={"role": ["Only the Owner can grant the Administrator role."]}, response_only=True),
    ],
)


def _update_member_schema(summary: str):
    return extend_schema(
        summary=summary,
        description=(
            "Changes a member's **role** and/or **active** flag. **Administrators and Owners** only.\n\n"
            "Rules: the Owner's membership can't be changed; only the Owner can change an Administrator or "
            "grant the Administrator role; the Owner role is never assignable; nobody can change their own "
            "membership; a role must belong to the membership's organization."
        ),
        request=MembershipUpdateSerializer,
        responses={
            200: OpenApiResponse(response=MembershipSerializer, description="The updated membership."),
            400: _MEMBERSHIP_RULES_400,
            401: UNAUTHENTICATED,
            403: FORBIDDEN_MANAGER,
            404: NOT_FOUND,
        },
    )


update_member_schema = _update_member_schema("Update a membership")
partial_update_member_schema = _update_member_schema("Partially update a membership")

delete_member_schema = extend_schema(
    summary="Remove a member or revoke an invitation",
    description=(
        "**Administrators and Owners** only. A pending invitation is **deleted**; an accepted member is "
        "**deactivated, not deleted**, so history and audit references stay intact, and they immediately "
        "lose access to the organization. The same protections as updating apply (Owner, own membership, "
        "Administrators). There is no self-service leave endpoint yet."
    ),
    request=None,
    responses={
        204: OpenApiResponse(description="Removed."),
        400: _MEMBERSHIP_RULES_400,
        401: UNAUTHENTICATED,
        403: FORBIDDEN_MANAGER,
        404: NOT_FOUND,
    },
)


# ===========================================================================
# Invitations
# ===========================================================================
invite_member_schema = extend_schema(
    tags=["Invitations"],
    summary="Invite a member by email",
    description=(
        "Emails an invitation to join an organization. Only **Administrators and Owners of that "
        "organization** can invite; the organization must be one the caller belongs to.\n\n"
        "Rules:\n"
        "- the role must belong to the organization; the **Owner** role can never be granted by "
        "invitation, and only the **Owner** can invite an **Administrator**;\n"
        "- someone who is already a member, or already has an unexpired invitation, can't be invited again;\n"
        "- an **expired** invitation to the same email is refreshed (new token and expiry) instead of duplicated;\n"
        "- pending invitations count against the plan's **user limit**.\n\n"
        "The email is sent only after the invitation is saved."
    ),
    request=InvitationCreateSerializer,
    examples=[
        OpenApiExample(
            "Invite a front-desk user",
            request_only=True,
            value={"organization": 1, "role": 7, "invited_email": "new.hire@techify.com"},
        )
    ],
    responses={
        201: OpenApiResponse(response=InvitationSentSerializer, description="Invitation created and emailed."),
        400: OpenApiResponse(
            description="Unknown organization or role, already a member, already invited, or a role rule was broken.",
            examples=[
                OpenApiExample("Already invited", value={"invited_email": ["This email is already invited to the organization."]}, response_only=True),
                OpenApiExample("Owner role", value={"role": ["The Owner role cannot be assigned to another member."]}, response_only=True),
            ],
        ),
        401: UNAUTHENTICATED,
        403: OpenApiResponse(
            description="Not an Administrator/Owner of the organization, or the plan's user limit is reached.",
            examples=[
                OpenApiExample(
                    "Plan limit",
                    value={"detail": "Your plan allows up to 5 users. Upgrade your plan to add more."},
                    response_only=True,
                )
            ],
        ),
    },
)

validate_invite_schema = extend_schema(
    tags=["Invitations"],
    summary="Validate an invitation token",
    description=(
        "**Public** (no login needed). Lets the signup/login screen show *who invited you, to what, and "
        "as whom* before the invitee authenticates. Returns only the organization name, role name, invited "
        "email and expiry: nothing else about the organization or its members.\n\n"
        "Rate limited. Unknown, used or expired tokens, and deactivated organizations, all return the same "
        "kind of 400."
    ),
    auth=[],
    parameters=[
        OpenApiParameter(
            name="token",
            type=OpenApiTypes.UUID,
            location=OpenApiParameter.QUERY,
            required=True,
            description="Invitation token from the email link.",
        )
    ],
    responses={
        200: OpenApiResponse(
            response=InvitationPreviewSerializer,
            description="The invitation is valid.",
            examples=[
                OpenApiExample(
                    "Valid invitation",
                    value={
                        "organization": "Okafor & Sons Stores",
                        "role": "Front Desk",
                        "invited_email": "new.hire@techify.com",
                        "expires_at": "2026-09-28T08:15:00Z",
                    },
                    response_only=True,
                )
            ],
        ),
        400: OpenApiResponse(
            description="Malformed, unknown, used or expired token.",
            examples=[OpenApiExample("Expired", value={"non_field_errors": ["This invitation has expired."]}, response_only=True)],
        ),
        429: OpenApiResponse(description="Too many requests."),
    },
)

accept_invite_schema = extend_schema(
    tags=["Invitations"],
    summary="Accept an invitation",
    description=(
        "Joins the organization using an invitation token.\n\n"
        "Requirements:\n"
        "- the user is **signed in** with a **verified** email address;\n"
        "- that email **matches** the invited address;\n"
        "- the token is unused and unexpired, the organization is active, and the user isn't already a member.\n\n"
        "Workflow: the invitee signs up or logs in with the invited email, verifies it, then calls this "
        "endpoint with the token. Returns the new membership."
    ),
    request=AcceptInvitationSerializer,
    examples=[
        OpenApiExample(
            "Accept",
            request_only=True,
            value={"token": "4f0c7c52-6b1f-4b53-9c1a-3d2f6f1b9a10"},
        )
    ],
    responses={
        200: OpenApiResponse(response=MembershipSerializer, description="Invitation accepted; the membership is now active."),
        400: OpenApiResponse(
            description="The invitation can't be accepted.",
            examples=[
                OpenApiExample("Wrong account", value={"non_field_errors": ["This invitation was not sent to your email address."]}, response_only=True),
                OpenApiExample("Unverified email", value={"non_field_errors": ["Verify your email address before accepting an invitation."]}, response_only=True),
            ],
        ),
        401: UNAUTHENTICATED,
    },
)
