from django.core.exceptions import ValidationError as DjangoValidationError
from drf_spectacular.utils import extend_schema
from rest_framework import mixins
from rest_framework import status
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import MethodNotAllowed
from rest_framework.exceptions import NotFound
from rest_framework.exceptions import PermissionDenied
from rest_framework.exceptions import ValidationError
from rest_framework.filters import OrderingFilter
from rest_framework.filters import SearchFilter
from rest_framework.permissions import AllowAny
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import SimpleRateThrottle
from rest_framework.throttling import UserRateThrottle
from rest_framework.viewsets import GenericViewSet
from rest_framework.viewsets import ModelViewSet
from rest_framework.viewsets import ReadOnlyModelViewSet

from core.applications.notification.audit.context import AuditContext
from core.applications.users import invitation_services
from core.applications.users import reference_data
from core.applications.users import services
from core.applications.users.api.schemas.initations import invitation_schema
from core.applications.users.api.schemas.initations import (
    organization_invitation_schema,
)
from core.applications.users.api.schemas.memberships import membership_schema
from core.applications.users.api.schemas.organizations import organization_schema
from core.applications.users.api.schemas.refrence import business_type_schema
from core.applications.users.api.schemas.refrence import metadata_schema
from core.applications.users.api.serializers.organization_serializers import (
    AcceptInvitationSerializer,
)
from core.applications.users.api.serializers.organization_serializers import (
    BusinessTypeSerializer,
)
from core.applications.users.api.serializers.organization_serializers import (
    InvitationAcceptedSerializer,
)
from core.applications.users.api.serializers.organization_serializers import (
    InvitationCreateSerializer,
)
from core.applications.users.api.serializers.organization_serializers import (
    InvitationPreviewSerializer,
)
from core.applications.users.api.serializers.organization_serializers import (
    InvitationRevokeSerializer,
)
from core.applications.users.api.serializers.organization_serializers import (
    InvitationSerializer,
)
from core.applications.users.api.serializers.organization_serializers import (
    LocationOptionSerializer,
)
from core.applications.users.api.serializers.organization_serializers import (
    MembershipLocationsSerializer,
)
from core.applications.users.api.serializers.organization_serializers import (
    MembershipSerializer,
)
from core.applications.users.api.serializers.organization_serializers import (
    MembershipUpdateSerializer,
)
from core.applications.users.api.serializers.organization_serializers import (
    OptionSerializer,
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
    RoleOptionSerializer,
)
from core.applications.users.api.serializers.organization_serializers import (
    RoleSerializer,
)
from core.applications.users.api.serializers.organization_serializers import (
    StateOptionSerializer,
)
from core.applications.users.api.serializers.organization_serializers import (
    StatesQuerySerializer,
)
from core.applications.users.errors import domain_errors
from core.applications.users.models import BusinessType
from core.applications.users.models import Invitation
from core.applications.users.models import Membership
from core.applications.users.models import Organization
from core.applications.users.models import Role
from core.applications.users.models import State
from core.applications.users.permissions import IsMembershipManager
from core.helper.enums import InvitationStatus
from core.helper.enums import PermissionCode
from core.helper.patterns import UUID_RE
from core.helper.permissions import IsOrganizationAdminOrOwner
from core.helper.permissions import IsOrganizationOwner

STATIC_MAX_AGE = 60 * 60 * 24
STATES_MAX_AGE = 60 * 60


def _cacheable(data, max_age: int) -> Response:
    """Reference data is identical for every user, so let the browser cache it."""
    response = Response(data)
    response["Cache-Control"] = f"private, max-age={max_age}"
    return response


def _audit_context(request) -> AuditContext:
    """Single place that builds the audit context, so a signature change is a one-line fix."""
    return AuditContext.from_request(request)


def _get_actor_membership(user, organization: Organization) -> Membership | None:
    """The caller's effective membership in `organization`, with what the scope rules need preloaded."""
    return (
        Membership.objects.effective()
        .for_organization(organization)
        .for_user(user)
        .select_related("role")
        .prefetch_related("locations")
        .first()
    )


# ===========================================================================
# Business types
# ===========================================================================

@business_type_schema
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

    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)


# ===========================================================================
# Organizations
# ===========================================================================

@organization_schema
@extend_schema(tags=["Organizations"])
class OrganizationViewSet(ModelViewSet):
    """
    API endpoint for managing Organizations.

    - list: View all organizations the user belongs to.
    - retrieve: View a single organization with details.
    - create: Create a new organization (auto-links creator as Owner and creates the default location).
    - update/partial_update: Update organization settings (Admins + Owners only).
    - business_types: Replace the organization's business types (Admins + Owners only).
    - members: List organization members (Admins + Owners only). Pending invitations
      are listed under /organizations/{id}/invitations/.
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
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    def create(self, request, *args, **kwargs):
        return super().create(request, *args, **kwargs)

    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        return super().partial_update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        # Business records are never hard-deleted (PRD §44); deleting a tenant
        # would cascade through everything it owns. Use the deactivate action.
        raise MethodNotAllowed(request.method, detail="Organizations cannot be deleted. Use the deactivate action.")

    # -- actions ------------------------------------------------------------
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

    @action(detail=True, methods=["delete"], url_path="deactivate")
    def deactivate(self, request, pk=None):
        """Deactivate an organization (Owners only)."""
        organization = self.get_object()
        services.deactivate_organization(organization=organization, actor=request.user)
        return Response({"detail": f"Organization '{organization.name}' deactivated."})

    @action(detail=True, methods=["get"])
    def members(self, request, pk=None):
        """
        List members of an organization, with their role and location access.
        Example: GET /api/v1/organizations/{id}/members/
        """
        organization = self.get_object()
        memberships = (
            Membership.objects.for_organization(organization)
            .with_user_and_role()
            .prefetch_related("locations")
            .order_by("created_at", "pk")
        )

        page = self.paginate_queryset(memberships)
        if page is not None:
            return self.get_paginated_response(self.get_serializer(page, many=True).data)
        return Response(self.get_serializer(memberships, many=True).data)

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
@membership_schema
@extend_schema(tags=["Memberships"])
class MembershipViewSet(ModelViewSet):
    """
    Manage organization memberships.

    Visibility:
        - Users can see their own memberships.
        - Organization Owners/Administrators can see memberships
          belonging to organizations they manage.

    Membership creation happens exclusively through the invitation workflow
    (POST /organizations/{organization_id}/invitations/). Business operations
    are delegated to the service layer.
    """

    queryset = Membership.objects.all()
    permission_classes = [IsAuthenticated]

    action_permissions = {
        "update": (IsMembershipManager,),
        "partial_update": (IsMembershipManager,),
        "destroy": (IsMembershipManager,),
        "set_locations": (IsMembershipManager,),
    }

    def get_queryset(self):
        queryset = (
            Membership.objects
            .visible_to(self.request.user)
            .with_user_and_role()
            .select_related("organization")
            .prefetch_related("locations")
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
        if self.action in {"update", "partial_update"}:
            return MembershipUpdateSerializer
        if self.action == "set_locations":
            return MembershipLocationsSerializer
        return MembershipSerializer

    def get_permissions(self):
        extra_permissions = self.action_permissions.get(self.action, ())
        return [IsAuthenticated(), *(permission() for permission in extra_permissions)]

    def create(self, request, *args, **kwargs):
        raise MethodNotAllowed(
            request.method,
            detail=(
                "Direct membership creation is not allowed. "
                "Use POST /organizations/{organization_id}/invitations/."
            ),
        )

    def destroy(self, request, *args, **kwargs):
        membership = self.get_object()

        with domain_errors():
            services.remove_membership(membership=membership, actor=request.user)

        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["put"], url_path="locations")
    def set_locations(self, request, pk=None):
        """
        Replace the locations a member may work in.
        Body: {"all_locations": true} or {"all_locations": false, "locations": ["<id>", ...]}
        """
        membership = self.get_object()  # tenant-scoped + IsMembershipManager

        actor_membership = _get_actor_membership(request.user, membership.organization)
        if actor_membership is None:
            raise PermissionDenied

        serializer = self.get_serializer(
            data=request.data,
            context={
                **self.get_serializer_context(),
                "organization": membership.organization,
                "actor_membership": actor_membership,
            },
        )
        serializer.is_valid(raise_exception=True)

        with domain_errors():
            invitation_services.update_membership_locations(
                membership=membership,
                actor=request.user,
                all_locations=serializer.validated_data["all_locations"],
                locations=serializer.validated_data.get("locations", []),
                audit_context=_audit_context(request),
            )

        membership = self.get_queryset().get(pk=membership.pk)  # fresh, locations preloaded
        return Response(MembershipSerializer(membership, context=self.get_serializer_context()).data)


# ===========================================================================
# Invitations: team side (authenticated, organization-scoped)
# ===========================================================================
class OrganizationContextMixin:
    """
    For viewsets nested under /organizations/{organization_id}/...

    Resolves the tenant from the URL (never from the body), enforces that the
    caller is an active member (outsiders get 404, so existence isn't leaked),
    gates each action on a permission code (PRD §29, API layer), and exposes
    `organization` and `actor_membership` to views and serializers.
    The service layer re-checks everything; this is the second line of defence.
    """

    organization_url_kwarg = "organization_id"
    default_permission_code: PermissionCode | None = None
    action_permission_codes: dict[str, PermissionCode] = {}  # noqa: RUF012

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)  # authentication + IsAuthenticated

        try:
            self.organization = Organization.objects.for_user(request.user).get(
                pk=kwargs[self.organization_url_kwarg]
            )
        except (Organization.DoesNotExist, DjangoValidationError, ValueError):
            raise NotFound from None

        self.actor_membership = _get_actor_membership(request.user, self.organization)
        if self.actor_membership is None:
            raise NotFound

        code = self.action_permission_codes.get(self.action, self.default_permission_code)
        if code is not None and not services.has_org_permission(request.user, self.organization, code):
            raise PermissionDenied

    def get_serializer_context(self):
        context = super().get_serializer_context()
        # Absent during schema generation, where `initial` never runs.
        context["organization"] = getattr(self, "organization", None)
        context["actor_membership"] = getattr(self, "actor_membership", None)
        return context


@organization_invitation_schema
class OrganizationInvitationViewSet(
    OrganizationContextMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    GenericViewSet,
):
    """
    /organizations/{organization_id}/invitations/

    - list / retrieve: pending, accepted, revoked and expired invitations.
    - create: invite someone with a role and a location scope.
    - resend: rotate the token and extend the expiry.
    - revoke: cancel a pending invitation (the record is kept, PRD §44).
    - assignable-roles / assignable-locations: feed the invite form dropdowns.
    """

    permission_classes = [IsAuthenticated]
    queryset = Invitation.objects.all()  # schema/router inference only; get_queryset decides
    filter_backends = [SearchFilter, OrderingFilter]  # noqa: RUF012
    search_fields = ["name", "email"]  # noqa: RUF012
    ordering_fields = ["created_at", "expires_at", "name", "email"]  # noqa: RUF012
    ordering = ["-created_at"]  # noqa: RUF012
    # Anything other than a UUID is a 404 at the router, never a database error.
    lookup_value_regex = UUID_RE

    # Viewing the team needs VIEW_TEAM; changing invitations needs the invite/revoke codes.
    default_permission_code = PermissionCode.INVITE_TEAM_MEMBER
    action_permission_codes = {  # noqa: RUF012
        "list": PermissionCode.VIEW_TEAM,
        "retrieve": PermissionCode.VIEW_TEAM,
        "revoke": PermissionCode.REVOKE_INVITATION,
    }

    status_filters = {  # noqa: RUF012
        "pending": lambda qs: qs.pending(),
        "expired": lambda qs: qs.expired(),  # derived from expires_at, not stored
        "accepted": lambda qs: qs.filter(status=InvitationStatus.ACCEPTED),
        "revoked": lambda qs: qs.filter(status=InvitationStatus.REVOKED),
    }

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Invitation.objects.none()
        queryset = (
            Invitation.objects.for_organization(self.organization)
            .select_related("role", "invited_by")
            .prefetch_related("locations")
            .order_by("-created_at")
        )

        requested = self.request.query_params.get("status")
        if requested and self.action == "list":
            apply_filter = self.status_filters.get(requested)
            if apply_filter is None:
                raise ValidationError({"status": [f"Choose one of: {', '.join(self.status_filters)}."]})
            queryset = apply_filter(queryset)
        return queryset

    def get_serializer_class(self):
        if self.action == "create":
            return InvitationCreateSerializer
        if self.action == "revoke":
            return InvitationRevokeSerializer
        return InvitationSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        with domain_errors():
            issued = invitation_services.create_invitation(
                organization=self.organization,
                inviter=request.user,
                name=data["name"],
                email=data["email"],
                role=data["role"],
                all_locations=data["all_locations"],
                locations=data.get("locations", []),
                audit_context=_audit_context(request),
            )

        # Never return the raw token; it exists only in the email.
        invitation = self.get_queryset().get(pk=issued.invitation.pk)
        return Response(
            InvitationSerializer(invitation, context=self.get_serializer_context()).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["post"])
    def resend(self, request, **kwargs):
        invitation = self.get_object()  # tenant-scoped: another org's id is a 404

        with domain_errors():
            invitation_services.resend_invitation(
                organization=self.organization,
                invitation_id=invitation.pk,
                actor=request.user,
                audit_context=_audit_context(request),
            )

        invitation = self.get_queryset().get(pk=invitation.pk)
        return Response(InvitationSerializer(invitation, context=self.get_serializer_context()).data)

    @action(detail=True, methods=["post"])
    def revoke(self, request, **kwargs):
        invitation = self.get_object()

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        with domain_errors():
            invitation_services.revoke_invitation(
                organization=self.organization,
                invitation_id=invitation.pk,
                actor=request.user,
                reason=serializer.validated_data["reason"],
                audit_context=_audit_context(request),
            )

        invitation = self.get_queryset().get(pk=invitation.pk)
        return Response(InvitationSerializer(invitation, context=self.get_serializer_context()).data)

    @action(detail=False, methods=["get"], url_path="assignable-roles", pagination_class=None)
    def assignable_roles(self, request, **kwargs):
        roles = invitation_services.assignable_roles(self.organization, self.actor_membership)
        return Response(RoleOptionSerializer(roles, many=True).data)

    @action(detail=False, methods=["get"], url_path="assignable-locations", pagination_class=None)
    def assignable_locations(self, request, **kwargs):
        locations = invitation_services.assignable_locations(self.organization, self.actor_membership)
        return Response(LocationOptionSerializer(locations, many=True).data)


# ===========================================================================
# Invitations: public side (token holder, no organization context yet)
# ===========================================================================
class InvitationLookupThrottle(SimpleRateThrottle):
    """Per client IP, for everyone. AnonRateThrottle would skip logged-in callers."""

    scope = "invitation_lookup"

    def get_cache_key(self, request, view):
        return self.cache_format % {"scope": self.scope, "ident": self.get_ident(request)}


class InvitationAcceptThrottle(UserRateThrottle):
    scope = "invitation_accept"


@invitation_schema
class InvitationViewSet(GenericViewSet):
    """
    /invitations/validate/ and /invitations/accept/

    The token travels in the POST body, not the URL, so it never lands in
    access logs or browser history.
    """

    permission_classes = [IsAuthenticated]
    serializer_class = AcceptInvitationSerializer

    @action(
        detail=False,
        methods=["post"],
        permission_classes=[AllowAny],
        authentication_classes=[],  # a stale JWT must not turn a public lookup into a 401
        throttle_classes=[InvitationLookupThrottle],
    )
    def validate(self, request):
        """Minimal preview (organization, role, masked email, locations) shown before sign-up or login."""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        with domain_errors():
            invitation = invitation_services.get_invitation_preview(serializer.validated_data["token"])

        response = Response(InvitationPreviewSerializer(invitation).data)
        response["Cache-Control"] = "no-store"
        return response

    @action(detail=False, methods=["post"], throttle_classes=[InvitationAcceptThrottle])
    def accept(self, request):
        """Consume the invitation for the signed-in, email-verified user. Replaying it is safe."""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        with domain_errors():
            membership = invitation_services.accept_invitation(
                token=serializer.validated_data["token"],
                user=request.user,
                audit_context=_audit_context(request),
            )

        membership = (
            Membership.objects.select_related("organization", "role")
            .prefetch_related("locations")
            .get(pk=membership.pk)
        )
        return Response(InvitationAcceptedSerializer(membership).data)


# ===========================================================================
# Metadata
# ===========================================================================
@metadata_schema
@extend_schema(tags=["Metadata"])
class MetadataViewSet(viewsets.ViewSet):
    """Read-only reference data that feeds the onboarding dropdowns."""

    permission_classes = [IsAuthenticated]

    @extend_schema(responses=OptionSerializer(many=True))
    @action(detail=False, methods=["get"])
    def countries(self, request):
        return _cacheable(reference_data.get_countries(), STATIC_MAX_AGE)

    @extend_schema(responses=OptionSerializer(many=True))
    @action(detail=False, methods=["get"])
    def currencies(self, request):
        return _cacheable(reference_data.get_currencies(), STATIC_MAX_AGE)

    @extend_schema(
        parameters=[StatesQuerySerializer],
        responses=StateOptionSerializer(many=True),
    )
    @action(detail=False, methods=["get"])
    def states(self, request):
        query = StatesQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        states = State.objects.filter(country=query.validated_data["country"])
        return _cacheable(StateOptionSerializer(states, many=True).data, STATES_MAX_AGE)
