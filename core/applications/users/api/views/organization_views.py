
from django.core.exceptions import ValidationError as DjangoValidationError
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import MethodNotAllowed
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.viewsets import GenericViewSet
from rest_framework.viewsets import ModelViewSet
from rest_framework.viewsets import ReadOnlyModelViewSet

from core.applications.users import services
from core.applications.users.api.schemas import accept_invite_schema
from core.applications.users.api.schemas import business_types_organization_schema
from core.applications.users.api.schemas import create_organization_schema
from core.applications.users.api.schemas import deactivate_organization_schema
from core.applications.users.api.schemas import delete_member_schema
from core.applications.users.api.schemas import invite_member_schema
from core.applications.users.api.schemas import list_business_types_schema
from core.applications.users.api.schemas import list_members_schema
from core.applications.users.api.schemas import list_organization_schema
from core.applications.users.api.schemas import member_organization_schema
from core.applications.users.api.schemas import partial_update_member_schema
from core.applications.users.api.schemas import partial_update_organization_schema
from core.applications.users.api.schemas import retrieve_business_type_schema
from core.applications.users.api.schemas import retrieve_member_schema
from core.applications.users.api.schemas import retrieve_organization_schema
from core.applications.users.api.schemas import roles_organization_schema
from core.applications.users.api.schemas import update_member_schema
from core.applications.users.api.schemas import update_organization_schema
from core.applications.users.api.schemas import validate_invite_schema
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
    InvitationTokenSerializer,
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
from core.applications.users.errors import domain_errors
from core.applications.users.models import BusinessType
from core.applications.users.models import Membership
from core.applications.users.models import Organization
from core.applications.users.models import Role
from core.applications.users.permissions import IsMembershipManager
from core.helper.permissions import IsOrganizationAdminOrOwner
from core.helper.permissions import IsOrganizationOwner


# ===========================================================================
# Business types
# ===========================================================================
@extend_schema(tags=["Business Types"])
class BusinessTypeViewSet(ReadOnlyModelViewSet):
    """Catalog of business types. Read-only; managed by the seed migration/command and the admin."""

    permission_classes = [IsAuthenticated]
    serializer_class = BusinessTypeSerializer
    pagination_class = None
    lookup_field = "code"
    queryset = BusinessType.objects.all()  # schema/router inference only; get_queryset decides

    def get_queryset(self):
        if self.request.query_params.get("context") == "onboarding":
            return BusinessType.objects.for_onboarding().ordered()
        return BusinessType.objects.active().ordered()

    @list_business_types_schema
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @retrieve_business_type_schema
    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)


# ===========================================================================
# Organizations
# ===========================================================================
@extend_schema(tags=["Organizations"])
class OrganizationViewSet(ModelViewSet):
    """
    API endpoint for managing Organizations.

    - list: View all organizations the user belongs to.
    - retrieve: View a single organization with details.
    - create: Create a new organization (auto-links creator as Owner).
    - update/partial_update: Update organization settings (Admins + Owners only).
    - business_types: Replace the organization's business types (Admins + Owners only).
    - members: List organization members (Admins + Owners only).
    - roles: List the roles that can be assigned (Admins + Owners only).
    - deactivate: Soft-deactivate organization (Owners only).
    """

    queryset = Organization.objects.all()  # schema/router inference only; get_queryset decides
    permission_classes = [IsAuthenticated]

    # Extra guards per action, on top of IsAuthenticated. Object-level: they run
    # in get_object(), after the queryset has scoped to the caller's organizations
    # (so an outsider gets 404, a member without the role gets 403).
    action_permissions = {
        "update": (IsOrganizationAdminOrOwner,),
        "partial_update": (IsOrganizationAdminOrOwner,),
        "business_types": (IsOrganizationAdminOrOwner,),
        "members": (IsOrganizationAdminOrOwner,),
        "roles": (IsOrganizationAdminOrOwner,),
        "deactivate": (IsOrganizationOwner,),
    }

    def get_queryset(self):
        """Users only ever see active organizations they are accepted, active members of."""
        return Organization.objects.for_user(self.request.user).with_detail()

    def get_serializer_class(self):
        if self.action == "create":
            return OrganizationCreateSerializer
        if self.action in ("update", "partial_update"):
            return OrganizationUpdateSerializer
        if self.action == "business_types":
            return OrganizationBusinessTypesUpdateSerializer
        if self.action == "members":
            return OrganizationMemberSerializer
        if self.action == "roles":
            return RoleSerializer
        return OrganizationSerializer

    def get_permissions(self):
        extra = self.action_permissions.get(self.action, ())
        return [IsAuthenticated(), *(permission() for permission in extra)]

    # -- standard CRUD ------------------------------------------------------
    @list_organization_schema
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @create_organization_schema
    def create(self, request, *args, **kwargs):
        return super().create(request, *args, **kwargs)

    @retrieve_organization_schema
    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)

    @update_organization_schema
    def update(self, request, *args, **kwargs):
        return super().update(request, *args, **kwargs)

    @partial_update_organization_schema
    def partial_update(self, request, *args, **kwargs):
        return super().partial_update(request, *args, **kwargs)

    @extend_schema(exclude=True)
    def destroy(self, request, *args, **kwargs):
        # Business records are never hard-deleted (PRD §44); deleting a tenant
        # would cascade through everything it owns. Use the deactivate action.
        raise MethodNotAllowed(request.method, detail="Organizations cannot be deleted. Use the deactivate action.")

    # -- actions ------------------------------------------------------------
    @business_types_organization_schema
    @action(detail=True, methods=["put"], url_path="business-types")
    def business_types(self, request, pk=None):
        """
        Replace the organization's business types.
        Body: {"business_types": ["retail", "wholesale_distribution"], "primary_business_type": "retail"}
        """
        organization = self.get_object()  # also runs object-level permission checks

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with domain_errors():
            services.set_business_types(
                organization=organization,
                business_types=serializer.validated_data["business_types"],
                primary=serializer.validated_data["primary_business_type"],
            )

        organization = self.get_queryset().get(pk=organization.pk)  # fresh, fully preloaded
        return Response(OrganizationSerializer(organization, context=self.get_serializer_context()).data)

    @deactivate_organization_schema
    @action(detail=True, methods=["delete"], url_path="deactivate")
    def deactivate(self, request, pk=None):
        """Deactivate an organization (Owners only)."""
        organization = self.get_object()
        services.deactivate_organization(organization=organization, actor=request.user)
        return Response({"detail": f"Organization '{organization.name}' deactivated."})

    @member_organization_schema
    @action(detail=True, methods=["get"])
    def members(self, request, pk=None):
        """
        List members of an organization, including pending invitations.
        Example: GET /api/v1/organizations/{id}/members/
        """
        organization = self.get_object()
        memberships = Membership.objects.for_organization(organization).with_user_and_role().order_by("created_at", "pk")

        page = self.paginate_queryset(memberships)
        if page is not None:
            return self.get_paginated_response(self.get_serializer(page, many=True).data)
        return Response(self.get_serializer(memberships, many=True).data)

    @roles_organization_schema
    @action(detail=True, methods=["get"], pagination_class=None)
    def roles(self, request, pk=None):
        """
        List the roles that can be assigned in this organization.
        Example: GET /api/v1/organizations/{id}/roles/
        """
        organization = self.get_object()
        roles = Role.objects.for_organization(organization).ordered()
        return Response(self.get_serializer(roles, many=True).data)


# ===========================================================================
# Memberships
# ===========================================================================
@extend_schema(tags=["Memberships"])
class MembershipViewSet(ModelViewSet):
    """
    Memberships within organizations.

    Visibility: a user sees their own memberships, plus every membership of the
    organizations they manage (Owner/Administrator). Nothing else.
    Changing or removing members, and inviting, is for Owners and Administrators.
    """

    queryset = Membership.objects.all()  # schema/router inference only; get_queryset decides
    permission_classes = [IsAuthenticated]

    action_permissions = {
        "update": (IsMembershipManager,),
        "partial_update": (IsMembershipManager,),
        "destroy": (IsMembershipManager,),
    }

    def get_queryset(self):
        queryset = (
            Membership.objects.visible_to(self.request.user)
            .with_user_and_role()
            .select_related("organization")
            .order_by("-created_at", "pk")
        )

        organization_id = self.request.query_params.get("organization")
        if organization_id:
            try:
                organization_id = Organization._meta.pk.to_python(organization_id)
            except (DjangoValidationError, ValueError):
                raise ValidationError({"organization": "Invalid organization id."}) from None
            queryset = queryset.filter(organization_id=organization_id)
        return queryset

    def get_serializer_class(self):
        if self.action == "invite":
            return InvitationCreateSerializer
        if self.action in ("update", "partial_update"):
            return MembershipUpdateSerializer
        return MembershipSerializer

    def get_permissions(self):
        extra = self.action_permissions.get(self.action, ())
        return [IsAuthenticated(), *(permission() for permission in extra)]

    @list_members_schema
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @retrieve_member_schema
    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)

    @extend_schema(exclude=True)
    def create(self, request, *args, **kwargs):
        # A membership is only ever created by an invitation (which enforces the
        # role, seat and privilege rules), never directly.
        raise MethodNotAllowed(request.method, detail="Use POST /memberships/invite/ to add a member.")

    @update_member_schema
    def update(self, request, *args, **kwargs):
        return super().update(request, *args, **kwargs)

    @partial_update_member_schema
    def partial_update(self, request, *args, **kwargs):
        return super().partial_update(request, *args, **kwargs)

    @delete_member_schema
    def destroy(self, request, *args, **kwargs):
        membership = self.get_object()
        with domain_errors():
            services.remove_membership(membership=membership, actor=request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @invite_member_schema
    @action(detail=False, methods=["post"], url_path="invite")
    def invite(self, request):
        """
        Invite a new member to an organization.

        There is no object here, so no object-level permission can run: the
        organization must be one the caller belongs to (enforced by the
        serializer's scoped queryset) and the caller must be its Owner or
        Administrator (enforced in services.invite_member).
        """
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        membership = serializer.save()
        return Response(
            {"detail": "Invitation sent successfully.", "id": membership.pk},
            status=status.HTTP_201_CREATED,
        )


# ===========================================================================
# Invitations
# ===========================================================================
class InvitationValidateThrottle(AnonRateThrottle):
    """The validate endpoint is public, so it is rate limited (works without any DRF throttle settings)."""

    scope = "invitation_validate"
    rate = "30/min"


@extend_schema(tags=["Invitations"])
class InvitationViewSet(GenericViewSet):
    """
    Previewing and accepting invitations. Sending one is POST /memberships/invite/.

    - validate: Public. Show who invited you, to what, and as whom, before signing in.
    - accept: Signed-in user with a verified, matching email joins the organization.
    """

    permission_classes = [IsAuthenticated]
    serializer_class = AcceptInvitationSerializer
    queryset = Membership.objects.none()  # schema/router inference only

    @validate_invite_schema
    @action(
        detail=False,
        methods=["get"],
        url_path="validate",
        permission_classes=[AllowAny],
        throttle_classes=[InvitationValidateThrottle],
        serializer_class=InvitationTokenSerializer,
    )
    def validate(self, request):
        query = InvitationTokenSerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        with domain_errors():
            invitation = services.get_invitation_preview(query.validated_data["token"])
        return Response(InvitationPreviewSerializer(invitation).data)

    @accept_invite_schema
    @action(detail=False, methods=["post"], url_path="accept")
    def accept(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        membership = serializer.save()
        return Response(MembershipSerializer(membership, context=self.get_serializer_context()).data)
