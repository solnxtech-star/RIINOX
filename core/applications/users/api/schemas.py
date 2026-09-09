from drf_spectacular.utils import (
    extend_schema,
    OpenApiExample,
    OpenApiResponse,
    OpenApiTypes,
    OpenApiParameter,
)

from core.applications.users.api.serializers import (
    AcceptInvitationSerializer,
    InvitationCreateSerializer,
    MembershipSerializer,
    OrganizationCreateSerializer,
    OrganizationMemberSerializer,
    OrganizationUpdateSerializer,
    OrganizationSerializer,
    SubscriptionUpgradeSerializer,
)


# =========================
# Organization API Schemas
# =========================

list_organization_schema = extend_schema(
    summary="List Organizations",
    description=(
        "Retrieve a list of organizations that the authenticated user belongs to.\n\n"
        "Only **active memberships** are included in the response. "
        "Each organization entry contains metadata such as plan, customization, "
        "and member roles."
    ),
    responses={
        200: OpenApiResponse(
            response=OrganizationSerializer(many=True),
            description="List of organizations the user is part of.",
        ),
    },
)


create_organization_schema = extend_schema(
    summary="Create Organization",
    description=(
        "Create a new organization. The authenticated user is automatically assigned "
        "as the **Organization Admin**.\n\n"
        "New organizations are always placed on the **Free Plan** by default. "
        "Plan upgrades can only be performed later using the billing/subscription endpoints.\n\n"
        "Optional customization fields are available to personalize invoices and receipts:\n"
        "- **logo**: Upload a company logo.\n"
        "- **invoice_template**: Select an invoice template style (default = 'default').\n"
        "- **receipt_template**: Select a receipt template style (default = 'default').\n"
        "- **header_text**: Optional custom header (e.g., company slogan).\n"
        "- **footer_text**: Optional custom footer (e.g., payment instructions)."
    ),
    request=OrganizationCreateSerializer,
    responses={
        201: OpenApiResponse(
            response=OrganizationSerializer,
            description="Organization successfully created (Free Plan applied).",
        ),
        400: OpenApiResponse(description="Validation error. Check request payload."),
    },
)


retrieve_organization_schema = extend_schema(
    summary="Retrieve Organization",
    description=(
        "Retrieve details of a single organization by its unique identifier (ID). "
        "Includes information about the organization's plan, customization, and members."
    ),
    responses={
        200: OpenApiResponse(
            response=OrganizationSerializer,
            description="Organization details retrieved successfully.",
        ),
        404: OpenApiResponse(description="Organization not found."),
    },
)


update_organization_schema = extend_schema(
    summary="Update Organization",
    description=(
        "Update details of an existing organization. "
        "Only users with the **Admin** role in the organization are permitted.\n\n"
        "Supports updating organization name, customization settings, and branding."
    ),
    request=OrganizationUpdateSerializer,
    responses={
        200: OpenApiResponse(
            response=OrganizationSerializer,
            description="Organization successfully updated.",
        ),
        400: OpenApiResponse(description="Validation error."),
        403: OpenApiResponse(description="Permission denied. Only admins can update."),
    },
)


destroy_organization_schema = extend_schema(
    summary="Delete Organization",
    description=(
        "Soft-delete (deactivate) an organization by marking it as inactive. "
        "Only users with the **Admin** role in the organization are permitted.\n\n"
        "Deactivated organizations are not permanently deleted, but become inaccessible."
    ),
    responses={
        204: OpenApiResponse(description="Organization successfully deactivated."),
        403: OpenApiResponse(description="Permission denied. Only admins can delete."),
    },
)


member_organization_schema = extend_schema(
    summary="List Organization Members",
    description=(
        "Retrieve a list of all members in a given organization. "
        "Each member includes their associated **role** (Admin/Client/etc.) "
        "and active status."
    ),
    responses={
        200: OpenApiResponse(
            response=OrganizationMemberSerializer(many=True),
            description="List of organization members.",
            examples=[
                OpenApiExample(
                    "Sample Member List",
                    summary="Example response showing two members",
                    description="One Admin and one Client.",
                    value=[
                        {"user": "admin@techify.com", "role": "Admin", "active": True},
                        {"user": "staff@techify.com", "role": "Client", "active": True},
                    ],
                )
            ],
        ),
    },
)



validate_invite_schema = extend_schema(
    summary="Validate an invitation token",
    description=(
        "Checks if an invitation token is valid and not expired.\n\n"
        "This endpoint is **public** and allows the frontend to display the "
        "organization name, invited role, and email address before the user "
        "signs up or logs in.\n\n"
        "**Returns:** Organization details and invitation metadata."
    ),
    parameters=[
        OpenApiParameter(
            name="token",
            type=str,
            description="UUID invitation token included in the email.",
            required=True,
        )
    ],
    responses={
        200: OpenApiResponse(
            response=MembershipSerializer, description="Invitation token is valid."
        ),
        400: OpenApiResponse(description="Invalid or expired invitation."),
    },
)


accept_invite_schema = extend_schema(
    summary="Accept an invitation",
    description=(
        "Allows an invited user to accept their organization invitation.\n\n"
        "Requirements:\n"
        "- The user must be authenticated.\n"
        "- The authenticated user's email must match the invited email.\n"
        "- The token must be valid and not expired.\n\n"
        "**Workflow:**\n"
        "1. User signs up/logs in with the invited email.\n"
        "2. User calls this endpoint with the invitation token.\n"
        "3. Invitation is marked as accepted and linked to the user."
    ),
    request=AcceptInvitationSerializer,
    responses={
        200: OpenApiResponse(description="Invitation accepted successfully."),
        400: OpenApiResponse(description="Invalid request or expired token."),
    },
)


invite_member_schema = extend_schema(
    tags=["Invitations"],
    summary="Invite a new member",
    description=(
        "Allows an organization owner or admin to invite a new member.\n\n"
        "The invite is sent via email with a unique token. "
        "The invited user must sign up or log in with the invited email "
        "before accepting the invitation.\n\n"
        "**Rules:**\n"
        "- Only `Organization Owner` or users with `Admin` role in the organization can invite.\n"
        "- If the email has already been invited and not yet accepted, "
        "a new invite cannot be created."
    ),
    request=InvitationCreateSerializer,
    responses={
        201: OpenApiResponse(description="Invitation sent successfully."),
        400: OpenApiResponse(description="Validation error."),
        403: OpenApiResponse(description="Not authorized."),
    },
)


list_members_schema = extend_schema(
    summary="List memberships",
    description=(
        "Returns a list of memberships the authenticated user can access.\n\n"
        "- Admins may see all memberships within their organization.\n"
        "- Regular users see only their own memberships."
    ),
    responses={200: MembershipSerializer(many=True)},
)

retrieve_member_schema = extend_schema(
    summary="Retrieve a membership",
    description=(
        "Fetch details of a single membership by ID.\n\n"
        "Response includes user info, organization, role, and invitation status."
    ),
    responses={200: MembershipSerializer},
)


update_member_schema = extend_schema(
    summary="Update a membership",
    description=(
        "Update membership details (e.g., role or status).\n\n"
        "Only admins can update membership roles."
    ),
    request=MembershipSerializer,
    responses={
        200: MembershipSerializer,
        403: OpenApiResponse(description="Forbidden."),
    },
)

delete_member_schema = extend_schema(
    summary="Delete a membership",
    description=(
        "Remove a membership from the organization.\n\n"
        "- Only admins can delete other members.\n"
        "- Users may remove themselves from an organization."
    ),
    responses={
        204: OpenApiResponse(description="Membership deleted successfully."),
        403: OpenApiResponse(description="Forbidden."),
    },
)



subscription_upgrade_schema = extend_schema(
    summary="Upgrade Organization Subscription",
    description=(
        "Allows an **Organization Owner** to upgrade their subscription plan.\n\n"
        "- A `Payment` record will be created with `PENDING` status.\n"
        "- The user will need to complete payment via the chosen gateway.\n"
        "- Once payment is verified, the organization's plan will be updated."
    ),
    request=SubscriptionUpgradeSerializer,
    responses={
        201: OpenApiResponse(
            description="Payment initiated successfully. Awaiting confirmation.",
        ),
        400: OpenApiResponse(description="Validation error."),
        403: OpenApiResponse(description="Only Owners can upgrade subscriptions."),
        404: OpenApiResponse(description="Organization not found."),
    },
)
