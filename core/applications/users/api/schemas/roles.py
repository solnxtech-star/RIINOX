"""Roles & permissions: /organizations/{organization_id}/roles/ and /permissions/."""

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample
from drf_spectacular.utils import OpenApiParameter
from drf_spectacular.utils import OpenApiResponse
from drf_spectacular.utils import extend_schema
from drf_spectacular.utils import extend_schema_view

from core.applications.users.api.schemas.common import ERROR_FORMAT_NOTE
from core.applications.users.api.schemas.common import NOT_FOUND
from core.applications.users.api.schemas.common import ORGANIZATION_ID_PARAM
from core.applications.users.api.schemas.common import TEAM_AUTH_NOTE
from core.applications.users.api.schemas.common import UNAUTHENTICATED
from core.applications.users.api.schemas.common import coded_error
from core.applications.users.api.serializers import (
    organization_serializers as org_serializers,
)
from core.applications.users.errors import RoleErrorCode as R

TAGS = ["Roles & permissions"]

ROLE_ID_PARAM = OpenApiParameter(
    name="id", type=OpenApiTypes.UUID, location=OpenApiParameter.PATH, description="The role's `id`.",
)

_RULES = (
    "**Rules**\n"
    "- **Owner** and **Administrator** are locked (`is_locked: true`): they always have every permission. "
    "Show their checkboxes read-only.\n"
    "- Other **built-in** roles (Manager, Accountant, ...): permissions can be changed; name can't, and they "
    "can't be deleted (`can_rename: false`, `can_delete: false`).\n"
    "- **Custom** roles: rename, re-permission and delete freely, as long as nobody uses them for delete.\n"
    "- **No privilege escalation:** you can only add or remove permissions **you hold yourself** "
    "(`PERMISSION_ESCALATION`, listing the codes in `permissions`). The Owner holds everything.\n"
    "- Changes apply on the holders' **next request**: no logout needed. Every change is recorded in the "
    "audit log (`role.created`, `role.updated`, `role.permissions_changed`, `role.deleted`).\n"
)

_ROLE_EXAMPLE = {
    "id": "7d0c5b1e-2a4f-4c6e-9b3a-1f2e3d4c5b6a",
    "name": "Cashier",
    "slug": "cashier",
    "description": "Front counter: sells and takes payments.",
    "is_system": False,
    "permissions": ["CREATE_SALE", "RECORD_PAYMENT", "VIEW_CUSTOMERS", "VIEW_PRODUCTS", "VIEW_SALES"],
    "member_count": 3,
    "is_locked": False,
    "can_rename": True,
    "can_delete": False,
}

_ROLE_RESPONSE = OpenApiResponse(
    org_serializers.RoleDetailSerializer, description="The role.",
    examples=[OpenApiExample("Custom role", value=_ROLE_EXAMPLE, response_only=True)],
)

_FORBIDDEN_VIEW = OpenApiResponse(description="The caller's role does not grant `VIEW_ROLES`.")
_FORBIDDEN_MANAGE = coded_error(
    "The caller's role does not grant `MANAGE_ROLES`, the role is locked, or the change would grant or "
    "remove a permission the caller doesn't hold.",
    ("Owner/Administrator", R.ROLE_LOCKED,
     "The Owner and Administrator roles always have every permission and can't be changed."),
    ("Escalation", R.PERMISSION_ESCALATION,
     "You can only add or remove permissions you hold yourself. Not allowed: MANAGE_BILLING."),
)
_NOT_FOUND_ROLE = coded_error("No role with this id in this organization.", ("Not found", R.ROLE_NOT_FOUND, "Role not found."))
_BAD_PERMISSION = coded_error(
    "A rule about the name or the permission codes was broken (a missing `name` field is a "
    "`VALIDATION_ERROR` with `errors.name`).",
    ("Unknown code", R.UNKNOWN_PERMISSION, "Unknown permission code(s): VIEW_EVERYTHING."),
    ("Empty name", R.ROLE_NAME_REQUIRED, "Enter a role name."),
)

role_schema = extend_schema_view(
    list=extend_schema(
        tags=TAGS,
        summary="List roles",
        description=(
            "Every role of the organization, built-in first then custom, alphabetically, with its permission "
            "codes and how many **active** members hold it. Not paginated (an organization has a handful).\n\n"
            "Use it for the **Roles & Permissions** screen. For the *Assign role* dropdown of the invite form use "
            "`GET /organizations/{id}/invitations/assignable-roles/` instead: it removes roles the caller "
            "can't hand out.\n\n"
            "**Permission:** `VIEW_ROLES`.\n\n" + TEAM_AUTH_NOTE
        ),
        parameters=[ORGANIZATION_ID_PARAM],
        responses={
            200: OpenApiResponse(
                org_serializers.RoleDetailSerializer(many=True), description="The organization's roles.",
                examples=[OpenApiExample("Roles", value=[_ROLE_EXAMPLE], response_only=True)],
            ),
            401: UNAUTHENTICATED, 403: _FORBIDDEN_VIEW, 404: NOT_FOUND,
        },
    ),
    retrieve=extend_schema(
        tags=TAGS,
        summary="Retrieve a role",
        description="One role with its permission codes. **Permission:** `VIEW_ROLES`.",
        parameters=[ORGANIZATION_ID_PARAM, ROLE_ID_PARAM],
        responses={200: _ROLE_RESPONSE, 401: UNAUTHENTICATED, 403: _FORBIDDEN_VIEW, 404: NOT_FOUND},
    ),
    create=extend_schema(
        tags=TAGS,
        summary="Create a custom role",
        description=(
            "Creates a custom role. `slug` is generated from the name. Take permission codes from "
            "`GET /organizations/{id}/permissions/`; an empty list is allowed (a role with no access yet).\n\n"
            "The new role immediately appears in the invite form's role dropdown and in notification "
            "settings' role pickers.\n\n"
            "**Permission:** `MANAGE_ROLES`.\n\n" + _RULES + "\n" + ERROR_FORMAT_NOTE
        ),
        parameters=[ORGANIZATION_ID_PARAM],
        request=org_serializers.RoleWriteSerializer,
        examples=[
            OpenApiExample(
                "Cashier", request_only=True,
                value={"name": "Cashier", "description": "Front counter: sells and takes payments.",
                       "permissions": ["VIEW_PRODUCTS", "VIEW_CUSTOMERS", "VIEW_SALES", "CREATE_SALE",
                                       "RECORD_PAYMENT"]},
            ),
        ],
        responses={
            201: _ROLE_RESPONSE,
            400: _BAD_PERMISSION,
            401: UNAUTHENTICATED,
            403: _FORBIDDEN_MANAGE,
            404: NOT_FOUND,
            409: coded_error("Name already used (case-insensitive).",
                             ("Name taken", R.ROLE_NAME_TAKEN, "A role named 'Cashier' already exists.")),
        },
    ),
    partial_update=extend_schema(
        tags=TAGS,
        summary="Update a role",
        description=(
            "Send only what changes:\n"
            "- `name` / `description`: **custom roles only** (`SYSTEM_ROLE_NOT_RENAMEABLE` otherwise).\n"
            "- `permissions`: the **complete** new list; it replaces the current one (send `[]` to remove all). "
            "Allowed on custom and built-in roles, except Owner and Administrator.\n\n"
            "To make the checkbox screen simple: send the full list of ticked codes on Save.\n\n"
            "**Permission:** `MANAGE_ROLES`.\n\n" + _RULES + "\n" + ERROR_FORMAT_NOTE
        ),
        parameters=[ORGANIZATION_ID_PARAM, ROLE_ID_PARAM],
        request=org_serializers.RoleWriteSerializer,
        examples=[
            OpenApiExample("Change permissions", request_only=True,
                           value={"permissions": ["VIEW_PRODUCTS", "VIEW_SALES", "CREATE_SALE", "VIEW_TEAM"]}),
            OpenApiExample("Rename", request_only=True, value={"name": "Senior Cashier"}),
        ],
        responses={
            200: _ROLE_RESPONSE,
            400: coded_error(
                "A rule about the name or the permission codes was broken.",
                ("Built-in role renamed", R.SYSTEM_ROLE_NOT_RENAMEABLE,
                 "Built-in roles can't be renamed. You can change their permissions."),
                ("Unknown code", R.UNKNOWN_PERMISSION, "Unknown permission code(s): VIEW_EVERYTHING."),
            ),
            401: UNAUTHENTICATED,
            403: _FORBIDDEN_MANAGE,
            404: _NOT_FOUND_ROLE,
            409: coded_error("Name already used.", ("Name taken", R.ROLE_NAME_TAKEN,
                                                    "A role named 'Cashier' already exists.")),
        },
    ),
    destroy=extend_schema(
        tags=TAGS,
        summary="Delete a custom role",
        description=(
            "Deletes a **custom** role nobody uses. Refused with `ROLE_IN_USE` (409) while it has members "
            "(active or deactivated) or pending invitations: move them to another role first "
            "(`PATCH /memberships/{id}/`). The response then carries `members`, `pending_invitations` and "
            "`past_invitations` counts.\n\n"
            "A role that appears on **past** invitations is kept as history and can't be deleted; rename it "
            "(e.g. \"Old - Cashier\") and remove its permissions instead.\n\n"
            "**Permission:** `MANAGE_ROLES`, and you must hold every permission the role grants."
        ),
        parameters=[ORGANIZATION_ID_PARAM, ROLE_ID_PARAM],
        responses={
            204: OpenApiResponse(description="Deleted."),
            400: coded_error("Built-in roles can't be deleted.",
                             ("Built-in", R.SYSTEM_ROLE_NOT_DELETABLE, "Built-in roles can't be deleted.")),
            401: UNAUTHENTICATED,
            403: _FORBIDDEN_MANAGE,
            404: _NOT_FOUND_ROLE,
            409: OpenApiResponse(
                org_serializers.ErrorSerializer,
                description="The role is in use.",
                examples=[OpenApiExample("In use", response_only=True, value={
                    "code": "ROLE_IN_USE",
                    "detail": "This role is in use. Move its members and pending invitations to another role first.",
                    "members": 3, "pending_invitations": 1, "past_invitations": 0,
                })],
            ),
        },
    ),
)

permission_catalog_schema = extend_schema_view(
    list=extend_schema(
        tags=TAGS,
        summary="Permission catalog",
        description=(
            "Every permission a role can grant, grouped by module in display order (Dashboard, Products, "
            "Customers, ...). Build the role editor from it: one section per group (`label`), one checkbox per "
            "permission (`name`), and send the ticked `code`s as the role's `permissions`.\n\n"
            "The catalog is the same for every organization and changes only when the platform adds features. "
            "Not paginated.\n\n**Permission:** `VIEW_ROLES`."
        ),
        parameters=[ORGANIZATION_ID_PARAM],
        responses={
            200: OpenApiResponse(
                org_serializers.PermissionGroupSerializer(many=True),
                description="Permission groups.",
                examples=[OpenApiExample("Catalog", response_only=True, value=[{
                    "module": "products", "label": "Products",
                    "permissions": [
                        {"code": "VIEW_PRODUCTS", "name": "View Products", "description": ""},
                        {"code": "VIEW_PRODUCT_COSTS", "name": "View Product Costs", "description": ""},
                    ],
                }])],
            ),
            401: UNAUTHENTICATED, 403: _FORBIDDEN_VIEW, 404: NOT_FOUND,
        },
    ),
)
