"""Organizations: onboarding, profile, business types, members, roles."""

from drf_spectacular.utils import OpenApiExample
from drf_spectacular.utils import OpenApiResponse
from drf_spectacular.utils import extend_schema
from drf_spectacular.utils import extend_schema_view

from core.applications.users.api.schemas.common import FORBIDDEN_MANAGER, FORBIDDEN_OWNER, NOT_FOUND, UNAUTHENTICATED, DetailSerializer, error_examples
from core.applications.users.api.serializers import (
    organization_serializers as org_serializers,
)



_CREATE_BODY = (
    "| Field | Required | Meaning |\n"
    "|---|---|---|\n"
    "| `business_type` | yes | Code from `GET /business-type/?context=onboarding`. Becomes the primary type. |\n"
    "| `name` | yes | Trading name shown on documents. |\n"
    "| `country` | yes | Two-letter code from `GET /meta-data/countries/`. **Fixed after creation.** |\n"
    "| `currency` | yes | Code from `GET /meta-data/currencies/`. **Fixed after creation**: get it right the first time. |\n"
    "| `state` | yes | Name from `GET /meta-data/states/?country=<code>`. Free text only if that list is empty. |\n"
    "| `address` | yes | Business address. Also becomes the address of the default location. |\n"
    "| `staff_size` | yes | One of the staff-size options (for example `6-10`). |\n"
    "| `phone` | yes | Any local format; normalised to international format using the country. |\n"
    "| `postal_code`, `tax_id`, `registration_number` | no | Shown on documents when set. |\n"
)


def _update_organization(summary: str):
    return extend_schema(
        summary=summary,
        description=(
            "Updates the organization's profile and branding: name, contact details, address and postal code, "
            "tax ID, registration number, timezone, logo, header/footer and document templates. "
            "**Administrators and Owners** only.\n\n"
            "- `country` and `currency` are fixed after creation; sending them is ignored. Business types have "
            "their own endpoint.\n"
            "- `state` is validated against the organization's stored country, and `phone` is normalised using it.\n"
            "- **Tax settings** (PRD §7, §39): `default_tax_rate` (0-100, e.g. `7.50`) applies to taxable "
            "products without their own `tax_rate`; `0` means no tax. `prices_include_tax`: `true` = selling "
            "prices already contain tax (tax is shown as included, totals unchanged), `false` = tax is added on "
            "top. New organizations start at 0% and, for retail, supermarket and pharmacy, tax-inclusive. "
            "Changes apply to **new** sales and invoices only; existing documents keep their own setting "
            "(`prices_include_tax` on each sale/invoice).\n"
            "- A document template must be a shared default or one owned by this organization. **Custom "
            "templates** also need a plan that includes them.\n\n"
            "Returns the full organization."
        ),
        request=org_serializers.OrganizationUpdateSerializer,
        responses={
            200: OpenApiResponse(response=org_serializers.OrganizationSerializer, description="The updated organization."),
            400: error_examples(
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
            "Organizations the signed-in user is an **active member** of. Deactivated organizations and "
            "memberships are excluded. Use it to build the organization switcher; when the user switches, "
            "discard any cached organization-scoped data.\n\n"
            "Each entry includes the plan and the business types (primary first). Members are not embedded: use "
            "`GET /organizations/{id}/members/`."
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
            "Creates a tenant from the two onboarding screens (business type and business details), submitted "
            "together so no half-created organization can exist.\n\n"
            "**Request body**\n\n" + _CREATE_BODY + "\n"
            "**What is set up automatically, in one transaction**\n"
            "- the caller becomes the organization's **Owner**;\n"
            "- the chosen type becomes the **primary business type** (change or add more later with "
            "`PUT /organizations/{id}/business-types/`);\n"
            "- the built-in roles and the **Free plan** subscription;\n"
            "- a **default location** (\"Main Branch\") using the business address, so single-site businesses "
            "never have to create one;\n"
            "- document numbering and the shared default invoice and receipt templates.\n\n"
            "Branding and templates are configured afterwards with `PATCH /organizations/{id}/`."
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
            400: error_examples(
                "Validation error. Field-level checks run first; the state and phone checks run only once those "
                "pass, so they can appear in a later response.",
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
            "One organization with its plan and business types. Available to every active member. Members, roles "
            "and invitations have their own endpoints."
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
            "Replaces the **full set** of business types: send every type the organization should have and mark "
            "exactly one as primary. The primary type drives terminology and dashboard defaults. Sending the same "
            "body twice changes nothing. **Administrators and Owners** only.\n\n"
            "| Field | Meaning |\n"
            "|---|---|\n"
            "| `business_types` | Array of codes from `GET /business-type/`. At least one. |\n"
            "| `primary_business_type` | One of the codes in `business_types`. |\n"
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
            400: error_examples(
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
            "(`DELETE /organizations/{id}/` returns `405`): every business record is kept, but the organization "
            "disappears from all members' lists and from the API. Reactivation is a support action.\n\n"
            "Calling it again returns `404`, because the organization is no longer visible."
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
            "The organization's **members**: people who have accepted an invitation (or created the "
            "organization). Pending invitations are **not** listed here: use "
            "`GET /organizations/{id}/invitations/` for those.\n\n"
            "Each member includes their role and **location access** (`all_locations`, plus `locations` when it "
            "is `false`). Show \"All locations\" when `all_locations` is `true`, otherwise the location names. "
            "Ordered by when they joined. Paginated. **Administrators and Owners** only."
        ),
        responses={
            200: OpenApiResponse(
                response=org_serializers.OrganizationMemberSerializer(many=True),
                description="Members of the organization.",
                examples=[
                    OpenApiExample(
                        "An Owner and a branch-limited manager",
                        value=[
                            {
                                "id": "0c9d3f1e-6f6b-4a53-8e5a-0e2a9a3a1b10",
                                "user": "owner@techify.com",
                                "user_name": "Chidi Okafor",
                                "joined_at": "2026-09-01T09:00:00Z",
                                "role": "Owner",
                                "active": True,
                                "all_locations": True,
                                "locations": [],
                            },
                            {
                                "id": "6b1d2a77-4c1f-4d0e-9a0b-7c3f2b5e8d21",
                                "user": "manager@techify.com",
                                "user_name": "Ada Obi",
                                "joined_at": "2026-09-05T08:15:00Z",
                                "role": "Manager",
                                "active": True,
                                "all_locations": False,
                                "locations": [
                                    {
                                        "id": "a1b2c3d4-0000-4000-8000-000000000001",
                                        "name": "Lekki Branch",
                                        "code": "LEK",
                                        "location_type": "store",
                                        "is_default": False,
                                    }
                                ],
                            },
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
        summary="List the organization's roles",
        description=(
            "All roles defined for this organization (built-in and custom), for a roles management screen. "
            "**Administrators and Owners** only.\n\n"
            "To fill the *Assign role* dropdown of the invite form, use "
            "`GET /organizations/{id}/invitations/assignable-roles/` instead: it already removes the roles the "
            "current user is not allowed to hand out."
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
