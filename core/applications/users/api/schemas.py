from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample
from drf_spectacular.utils import OpenApiParameter
from drf_spectacular.utils import OpenApiResponse
from drf_spectacular.utils import extend_schema
from drf_spectacular.utils import extend_schema_view
from drf_spectacular.utils import inline_serializer
from rest_framework import serializers

from core.applications.users.api.filters import INVITATION_STATUS_FILTERS
from core.applications.users.api.serializers import (
    organization_serializers as org_serializers,
)
from core.applications.users.errors import InvitationErrorCode as Err
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


def _error(description: str, *examples: tuple[str, dict]) -> OpenApiResponse:
    """A documented error response. Each example is a (name, response body) pair."""
    return OpenApiResponse(
        description=description,
        examples=[OpenApiExample(name, value=value, response_only=True) for name, value in examples],
    )


UNAUTHENTICATED = OpenApiResponse(description="Authentication credentials were not provided or are invalid.")
NOT_FOUND = OpenApiResponse(
    description="Not found. Also returned when the resource belongs to an organization the caller isn't a member of."
)
FORBIDDEN_MANAGER = _error(
    "The caller is a member but not an Administrator or Owner of this organization.",
    ("Not a manager", {"detail": "Only organization Administrators and Owners can perform this action."}),
)
FORBIDDEN_OWNER = _error(
    "The caller is a member but not the Owner of this organization.",
    ("Not the Owner", {"detail": "Only the organization Owner can perform this action."}),
)

ORGANIZATION_FILTER = OpenApiParameter(
    "organization",
    OpenApiTypes.STR,
    OpenApiParameter.QUERY,
    description="Only memberships of this organization.",
)


# ===========================================================================
# Business types
# ===========================================================================
business_type_schema = extend_schema_view(
    list=extend_schema(
        summary="List business types",
        description=(
            "The catalog of business types, ordered for display. Use `?context=onboarding` to get only the "
            "cards shown on the onboarding screen; without it you get every active type (for the later "
            '"edit business types" screen). `code` is the stable key the other endpoints use.'
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
        responses={
            200: OpenApiResponse(
                response=org_serializers.BusinessTypeSerializer(many=True),
                description="Business types.",
            ),
            401: UNAUTHENTICATED,
        },
    ),
    retrieve=extend_schema(
        summary="Retrieve a business type",
        description="One business type by its `code` (for example `wholesale_distribution`).",
        responses={
            200: OpenApiResponse(response=org_serializers.BusinessTypeSerializer, description="The business type."),
            401: UNAUTHENTICATED,
            404: NOT_FOUND,
        },
    ),
)


# ===========================================================================
# Organizations
# ===========================================================================
def _update_organization(summary: str):
    return extend_schema(
        summary=summary,
        description=(
            "Updates the organization's profile and branding: name, contact details, address and postal "
            "code, tax ID, CAC/RC number, timezone, logo, header/footer and document templates. "
            "**Administrators and Owners** only.\n\n"
            "- `country` and `currency` are fixed after creation, and business types have their own "
            "endpoint. Sending either field is ignored.\n"
            "- `state` is validated against the organization's stored country, and `phone` is normalised "
            "to international format using it.\n"
            "- A document template must be a shared default or one owned by this organization; "
            "**custom templates** additionally require a plan that includes them.\n\n"
            "Returns the full organization."
        ),
        request=org_serializers.OrganizationUpdateSerializer,
        responses={
            200: OpenApiResponse(response=org_serializers.OrganizationSerializer, description="The updated organization."),
            400: _error(
                "Validation error.",
                ("Invalid state for the organization's country", {"state": ["Select a valid state or region."]}),
                (
                    "Custom template not on plan",
                    {"invoice_template": ["Custom templates are not available on your current plan."]},
                ),
            ),
            401: UNAUTHENTICATED,
            403: FORBIDDEN_MANAGER,
            404: NOT_FOUND,
        },
    )


organization_schema = extend_schema_view(
    list=extend_schema(
        summary="List my organizations",
        description=(
            "Organizations the authenticated user is an **accepted, active member** of. "
            "Deactivated organizations and memberships are excluded. Each entry includes the plan "
            "and the organization's business types (primary first). Members are not embedded: "
            "use `GET /organizations/{id}/members/`."
        ),
        responses={
            200: OpenApiResponse(
                response=org_serializers.OrganizationSerializer(many=True),
                description="Organizations the user belongs to.",
            ),
            401: UNAUTHENTICATED,
        },
    ),
    create=extend_schema(
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
            "Where the values come from:\n"
            "- `business_type`: `GET /business-types/?context=onboarding`.\n"
            "- `country`, `currency`: `GET /metadata/countries/` and `GET /metadata/currencies/` "
            "(ISO codes). **Both are fixed after creation**, so the currency must be right the first time.\n"
            "- `state`: `GET /metadata/states/?country=<code>`. It must match one of the listed states; if "
            "that list is empty for the chosen country, any text is accepted.\n\n"
            "`phone` is optional and normalised to international format using the organization's country "
            "(`0803 123 4567` becomes `+2348031234567` for `NG`). Branding and templates are configured "
            "later with `PATCH /organizations/{id}/`."
        ),
        request=org_serializers.OrganizationCreateSerializer,
        examples=[
            OpenApiExample(
                "Retail shop in Nigeria",
                request_only=True,
                value={
                    "business_type": "retail",
                    "name": "Okafor & Sons Stores",
                    "registration_number": "RC1234567",
                    "country": "NG",
                    "state": "Rivers",
                    "address": "12 Aba Road, Port Harcourt",
                    "postal_code": "500001",
                    "tax_id": "2345678901",
                    "currency": "NGN",
                    "staff_size": "6-10",
                },
            )
        ],
        responses={
            201: OpenApiResponse(
                response=org_serializers.OrganizationSerializer,
                description="Organization created; the caller is its Owner.",
            ),
            400: _error(
                "Validation error. Field-level checks run first; the state and phone checks run only once "
                "those pass, so they are reported in a later response.",
                (
                    "Invalid field values",
                    {
                        "business_type": ["Object with code=nope does not exist."],
                        "country": ["Select a valid country."],
                        "currency": ["Select a valid currency."],
                    },
                ),
                (
                    "Invalid state or phone for the country",
                    {
                        "state": ["Select a valid state or region."],
                        "phone": ["Enter a valid phone number."],
                    },
                ),
            ),
            401: UNAUTHENTICATED,
        },
    ),
    retrieve=extend_schema(
        summary="Retrieve an organization",
        description=(
            "A single organization with its plan and business types. Available to every active member "
            "of the organization. Members and roles have their own endpoints."
        ),
        responses={
            200: OpenApiResponse(response=org_serializers.OrganizationSerializer, description="The organization."),
            401: UNAUTHENTICATED,
            404: NOT_FOUND,
        },
    ),
    update=_update_organization("Update an organization"),
    partial_update=_update_organization("Partially update an organization"),
    destroy=extend_schema(exclude=True),
    business_types=extend_schema(
        summary="Replace an organization's business types",
        description=(
            "Replaces the **full set** of business types: send every type the organization should have "
            "and mark exactly one as primary. The primary type drives terminology and dashboard defaults. "
            "Idempotent: sending the same body twice changes nothing. **Administrators and Owners** only.\n\n"
            "Codes come from `GET /business-types/`."
        ),
        request=org_serializers.OrganizationBusinessTypesUpdateSerializer,
        examples=[
            OpenApiExample(
                "Retail and wholesale",
                request_only=True,
                value={"business_types": ["retail", "wholesale_distribution"], "primary_business_type": "retail"},
            )
        ],
        responses={
            200: OpenApiResponse(
                response=org_serializers.OrganizationSerializer,
                description="The organization with its new business types.",
            ),
            400: _error(
                "Empty selection, duplicates, unknown code, or a primary type that isn't in the selection.",
                (
                    "Primary not selected",
                    {"primary_business_type": ["The primary business type must be one of the selected types."]},
                ),
            ),
            401: UNAUTHENTICATED,
            403: FORBIDDEN_MANAGER,
            404: NOT_FOUND,
        },
    ),
    deactivate=extend_schema(
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
    ),
    members=extend_schema(
        summary="List organization members",
        description=(
            "Everyone in the organization, **including pending invitations** (`accepted: false`, with the "
            "invited email in `user`). Ordered by when they joined or were invited. Paginated when your "
            "project paginates by default. **Administrators and Owners** only."
        ),
        responses={
            200: OpenApiResponse(
                response=org_serializers.OrganizationMemberSerializer(many=True),
                description="Members and pending invitations.",
                examples=[
                    OpenApiExample(
                        "Owner, an administrator and a pending invitation",
                        value=[
                            {"id": 1, "user": "owner@techify.com", "role": "Owner", "active": True,
                             "accepted": True, "joined_at": "2026-09-01T09:00:00Z"},
                            {"id": 2, "user": "admin@techify.com", "role": "Administrator", "active": True,
                             "accepted": True, "joined_at": "2026-09-02T10:30:00Z"},
                            {"id": 3, "user": "new.hire@techify.com", "role": "Front Desk", "active": True,
                             "accepted": False, "joined_at": "2026-09-05T08:15:00Z"},
                        ],
                        response_only=True,
                    )
                ],
            ),
            401: UNAUTHENTICATED,
            403: FORBIDDEN_MANAGER,
            404: NOT_FOUND,
        },
    ),
    roles=extend_schema(
        summary="List assignable roles",
        description=(
            "Roles defined for this organization (built-in and custom); feeds the role picker when inviting "
            "or changing a member. The Owner role is listed but can never be assigned to another member. "
            "**Administrators and Owners** only."
        ),
        responses={
            200: OpenApiResponse(
                response=org_serializers.RoleSerializer(many=True),
                description="The organization's roles.",
            ),
            401: UNAUTHENTICATED,
            403: FORBIDDEN_MANAGER,
            404: NOT_FOUND,
        },
    ),
)


# ===========================================================================
# Memberships
# ===========================================================================
_MEMBERSHIP_RULES_400 = _error(
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
            "Changes a member's **role** and/or **active** flag. **Administrators and Owners** only.\n\n"
            "Rules: the Owner's membership can't be changed; only the Owner can change an Administrator or "
            "grant the Administrator role; the Owner role is never assignable; nobody can change their own "
            "membership; a role must belong to the membership's organization."
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
            "manage** (Owner or Administrator). Regular members therefore see only themselves. "
            "Filter with `?organization=`."
        ),
        parameters=[ORGANIZATION_FILTER],
        responses={
            200: OpenApiResponse(
                response=org_serializers.MembershipSerializer(many=True),
                description="Visible memberships.",
            ),
            400: _error(
                "Malformed `organization` filter.",
                ("Bad filter", {"organization": "Invalid organization id."}),
            ),
            401: UNAUTHENTICATED,
        },
    ),
    retrieve=extend_schema(
        summary="Retrieve a membership",
        description=(
            "One membership: user, organization, role, active/accepted status and (for pending invitations) "
            "the expiry. Only visible if it is the caller's own or belongs to an organization they manage."
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
    ),
    invite=extend_schema(
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
        request=org_serializers.InvitationCreateSerializer,
        examples=[
            OpenApiExample(
                "Invite a front-desk user",
                request_only=True,
                value={"organization": 1, "role": 7, "invited_email": "new.hire@techify.com"},
            )
        ],
        responses={
            201: OpenApiResponse(response=InvitationSentSerializer, description="Invitation created and emailed."),
            400: _error(
                "Unknown organization or role, already a member, already invited, or a role rule was broken.",
                ("Already invited", {"invited_email": ["This email is already invited to the organization."]}),
                ("Owner role", {"role": ["The Owner role cannot be assigned to another member."]}),
            ),
            401: UNAUTHENTICATED,
            403: _error(
                "Not an Administrator/Owner of the organization, or the plan's user limit is reached.",
                ("Plan limit", {"detail": "Your plan allows up to 5 users. Upgrade your plan to add more."}),
            ),
        },
    ),
)


# ===========================================================================
# Invitations
# ===========================================================================
invitation_schema = extend_schema_view(
    validate=extend_schema(
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
                response=org_serializers.InvitationPreviewSerializer,
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
            400: _error(
                "Malformed, unknown, used or expired token.",
                ("Expired", {"non_field_errors": ["This invitation has expired."]}),
            ),
            429: OpenApiResponse(description="Too many requests."),
        },
    ),
    accept=extend_schema(
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
        request=org_serializers.AcceptInvitationSerializer,
        examples=[
            OpenApiExample(
                "Accept",
                request_only=True,
                value={"token": "4f0c7c52-6b1f-4b53-9c1a-3d2f6f1b9a10"},
            )
        ],
        responses={
            200: OpenApiResponse(
                response=org_serializers.MembershipSerializer,
                description="Invitation accepted; the membership is now active.",
            ),
            400: _error(
                "The invitation can't be accepted.",
                ("Wrong account", {"non_field_errors": ["This invitation was not sent to your email address."]}),
                (
                    "Unverified email",
                    {"non_field_errors": ["Verify your email address before accepting an invitation."]},
                ),
            ),
            401: UNAUTHENTICATED,
        },
    ),
)


# ===========================================================================
# Metadata (onboarding dropdowns)
# ===========================================================================
def _cache_header(example: str) -> OpenApiParameter:
    return OpenApiParameter(
        "Cache-Control",
        OpenApiTypes.STR,
        OpenApiParameter.HEADER,
        response=[200],
        description=f"Browsers may cache this response (`{example}`).",
    )


metadata_schema = extend_schema_view(
    countries=extend_schema(
        summary="List countries",
        description=(
            "Every ISO 3166-1 country, sorted by name. `code` is the two-letter alpha-2 code the other "
            "endpoints accept (for example `country` when creating an organization). Static data, "
            "safe to cache."
        ),
        parameters=[_cache_header("private, max-age=86400")],
        responses={
            200: OpenApiResponse(
                response=org_serializers.OptionSerializer(many=True),
                description="Countries.",
                examples=[
                    OpenApiExample(
                        "Countries",
                        value=[{"code": "GH", "name": "Ghana"}, {"code": "NG", "name": "Nigeria"}],
                        response_only=True,
                    )
                ],
            ),
            401: UNAUTHENTICATED,
        },
    ),
    currencies=extend_schema(
        summary="List currencies",
        description=(
            "ISO 4217 currencies, sorted by name, feeding the **Base currency** dropdown. Precious metals, "
            "testing codes and supranational units (`XAU`, `XTS`, `XXX`...) are excluded. Static data, "
            "safe to cache."
        ),
        parameters=[_cache_header("private, max-age=86400")],
        responses={
            200: OpenApiResponse(
                response=org_serializers.OptionSerializer(many=True),
                description="Currencies.",
                examples=[
                    OpenApiExample(
                        "Currencies",
                        value=[{"code": "GHS", "name": "Ghana Cedi"}, {"code": "NGN", "name": "Naira"}],
                        response_only=True,
                    )
                ],
            ),
            401: UNAUTHENTICATED,
        },
    ),
    states=extend_schema(
        summary="List states or regions of a country",
        description=(
            "The states, provinces or regions of one country, feeding the **City/Region** dropdown. Reload "
            "it whenever the selected country changes.\n\n"
            "A valid country whose states haven't been added yet returns an **empty list**, not an error. "
            "In that case show a free-text field: the API accepts any state text for such countries."
        ),
        parameters=[org_serializers.StatesQuerySerializer, _cache_header("private, max-age=3600")],
        responses={
            200: OpenApiResponse(
                response=org_serializers.StateOptionSerializer(many=True),
                description="States of the country (possibly empty).",
                examples=[
                    OpenApiExample(
                        "Nigeria",
                        value=[{"name": "Abia", "code": "NG-AB"}, {"name": "Adamawa", "code": "NG-AD"}],
                        response_only=True,
                    )
                ],
            ),
            400: _error(
                "Missing or unknown country code.",
                ("Unknown country", {"country": ["Unknown country code."]}),
            ),
            401: UNAUTHENTICATED,
        },
    ),
)


class ErrorSerializer(serializers.Serializer):
    """Shape of every business-rule error (PRD §46). Used for API docs only."""

    code = serializers.CharField(help_text="Stable machine-readable error code.")
    detail = serializers.CharField(help_text="Human-readable message.")


def error_response(description: str) -> OpenApiResponse:
    return OpenApiResponse(ErrorSerializer, description=description)

PUBLIC_TAGS = ["Invitations"]
TEAM_TAGS = ["Team invitations"]


def _errors(*codes: Err):
    """Documents error responses from the real enum, so a renamed code can't leave stale docs."""
    return error_response(", ".join(code.value for code in codes))


# --------------------------------------------------------------------------- #
# Public side: InvitationViewSet
# Keys are the viewset's method names.
# --------------------------------------------------------------------------- #
INVITATION_SCHEMA = {
    "validate_invite": extend_schema(
        operation_id="invitations_validate",
        tags=PUBLIC_TAGS,
        summary="Preview an invitation",
        responses={
            200: org_serializers.InvitationPreviewSerializer,
            404: _errors(Err.INVITATION_INVALID),
            410: _errors(Err.INVITATION_EXPIRED),
            429: error_response("Rate limited."),
        },
    ),
    "accept_invite": extend_schema(
        operation_id="invitations_accept",
        tags=PUBLIC_TAGS,
        summary="Accept an invitation",
        request=org_serializers.AcceptInvitationSerializer,
        responses={
            200: org_serializers.InvitationAcceptedSerializer,
            403: _errors(
                Err.EMAIL_NOT_VERIFIED,
                Err.INVITATION_EMAIL_MISMATCH,
                Err.USER_LIMIT_REACHED,
            ),
            404: _errors(Err.INVITATION_INVALID),
            409: _errors(Err.ALREADY_MEMBER, Err.MEMBERSHIP_INACTIVE),
            410: _errors(Err.INVITATION_EXPIRED),
            429: error_response("Rate limited."),
        },
    ),
}


# --------------------------------------------------------------------------- #
# Team side: OrganizationInvitationViewSet
# --------------------------------------------------------------------------- #
ORGANIZATION_INVITATION_SCHEMA = {
    "list": extend_schema(
        operation_id="organization_invitations_list",
        tags=TEAM_TAGS,
        summary="List invitations",
        description="Invitations of this organization. Search by name or email; paginated.",
        parameters=[
            OpenApiParameter(
                "status",
                enum=list(INVITATION_STATUS_FILTERS),
                description="Filter by invitation status.",
            ),
        ],
        responses={
            200: org_serializers.InvitationSerializer(many=True),
            404: _errors(Err.NOT_ORGANIZATION_MEMBER),
        },
    ),
    "roles": extend_schema(
        operation_id="organization_invitations_roles",
        tags=TEAM_TAGS,
        summary="Roles the caller can assign",
        responses={
            200: org_serializers.RoleOptionSerializer(many=True),
            404: _errors(Err.NOT_ORGANIZATION_MEMBER),
        },
    ),
    "create": extend_schema(
        operation_id="organization_invitations_create",
        tags=TEAM_TAGS,
        summary="Invite a team member",
        request=org_serializers.InvitationCreateSerializer,
        responses={
            201: org_serializers.InvitationSerializer,
            403: _errors(Err.PERMISSION_DENIED, Err.ROLE_NOT_ASSIGNABLE, Err.USER_LIMIT_REACHED),
            404: _errors(Err.NOT_ORGANIZATION_MEMBER),
            409: _errors(Err.ALREADY_MEMBER, Err.MEMBERSHIP_INACTIVE, Err.ALREADY_INVITED),
        },
    ),
    "resend": extend_schema(
        operation_id="organization_invitations_resend",
        tags=TEAM_TAGS,
        summary="Resend an invitation",
        request=None,
        responses={
            200: org_serializers.InvitationSerializer,
            403: _errors(Err.PERMISSION_DENIED, Err.ROLE_NOT_ASSIGNABLE, Err.USER_LIMIT_REACHED),
            404: _errors(Err.NOT_ORGANIZATION_MEMBER, Err.INVITATION_NOT_FOUND),
            409: _errors(Err.INVITATION_NOT_PENDING),
            429: _errors(Err.INVITATION_RESEND_TOO_SOON),
        },
    ),
    "revoke": extend_schema(
        operation_id="organization_invitations_revoke",
        tags=TEAM_TAGS,
        summary="Revoke an invitation",
        request=org_serializers.InvitationRevokeSerializer,
        responses={
            200: org_serializers.InvitationSerializer,
            403: _errors(Err.PERMISSION_DENIED, Err.ROLE_NOT_ASSIGNABLE),
            404: _errors(Err.NOT_ORGANIZATION_MEMBER, Err.INVITATION_NOT_FOUND),
            409: _errors(Err.INVITATION_NOT_PENDING),
        },
    ),
}
