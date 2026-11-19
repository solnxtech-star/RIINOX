"""Roles & permissions screen: /organizations/{organization_id}/roles/ and /permissions/."""

from rest_framework import mixins
from rest_framework import status
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from core.applications.users import role_services
from core.applications.users.api.schemas.roles import permission_catalog_schema
from core.applications.users.api.schemas.roles import role_schema
from core.applications.users.api.serializers.organization_serializers import PermissionGroupSerializer
from core.applications.users.api.serializers.organization_serializers import RoleDetailSerializer
from core.applications.users.api.serializers.organization_serializers import RoleWriteSerializer
from core.applications.users.api.views.organization_views import OrganizationContextMixin
from core.applications.users.api.views.organization_views import _audit_context
from core.applications.users.models import Role
from core.helper.enums import PermissionCode
from core.helper.patterns import UUID_RE


def _detail(organization, role_id):
    return role_services.roles_with_usage(organization).get(pk=role_id)


@role_schema
class OrganizationRoleViewSet(
    OrganizationContextMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    GenericViewSet,
):
    """
    list / retrieve: VIEW_ROLES. create / partial_update / destroy: MANAGE_ROLES.
    Rules (locked roles, no escalation, in-use checks) live in role_services.
    """

    queryset = Role.objects.all()  # schema/router inference only; get_queryset decides
    serializer_class = RoleDetailSerializer
    pagination_class = None  # an organization has a handful of roles
    lookup_value_regex = UUID_RE
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    default_permission_code = PermissionCode.MANAGE_ROLES
    action_permission_codes = {  # noqa: RUF012
        "list": PermissionCode.VIEW_ROLES,
        "retrieve": PermissionCode.VIEW_ROLES,
    }

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Role.objects.none()
        return role_services.roles_with_usage(self.organization)

    def get_serializer_class(self):
        if self.action in ("create", "partial_update"):
            return RoleWriteSerializer
        return RoleDetailSerializer

    def create(self, request, *args, **kwargs):
        body = RoleWriteSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        role = role_services.create_role(
            organization=self.organization, actor=request.user, actor_membership=self.actor_membership,
            name=body.validated_data["name"], description=body.validated_data.get("description", ""),
            permissions=body.validated_data.get("permissions", []), audit_context=_audit_context(request),
        )
        data = RoleDetailSerializer(_detail(self.organization, role.pk)).data
        return Response(data, status=status.HTTP_201_CREATED)

    def partial_update(self, request, *args, **kwargs):
        body = RoleWriteSerializer(data=request.data, partial=True)
        body.is_valid(raise_exception=True)
        role = role_services.update_role(
            organization=self.organization, role_id=kwargs["pk"], actor=request.user,
            actor_membership=self.actor_membership, name=body.validated_data.get("name"),
            description=body.validated_data.get("description"),
            permissions=body.validated_data.get("permissions"), audit_context=_audit_context(request),
        )
        return Response(RoleDetailSerializer(_detail(self.organization, role.pk)).data)

    def destroy(self, request, *args, **kwargs):
        role_services.delete_role(
            organization=self.organization, role_id=kwargs["pk"], actor=request.user,
            actor_membership=self.actor_membership, audit_context=_audit_context(request),
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


@permission_catalog_schema
class OrganizationPermissionViewSet(OrganizationContextMixin, GenericViewSet):
    """GET /organizations/{id}/permissions/: the permission catalog, grouped by module. VIEW_ROLES."""

    serializer_class = PermissionGroupSerializer
    pagination_class = None
    default_permission_code = PermissionCode.VIEW_ROLES

    def get_queryset(self):  # not model-backed; satisfies GenericViewSet introspection
        return Role.objects.none()

    def list(self, request, *args, **kwargs):
        return Response(PermissionGroupSerializer(role_services.permission_catalog(), many=True).data)
