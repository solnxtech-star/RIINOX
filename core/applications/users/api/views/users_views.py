import logging
from smtplib import SMTPRecipientsRefused

from django.contrib.auth import logout
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth import user_logged_out
from django.utils import timezone
from django.utils.module_loading import import_string
from django.utils.timezone import now
from djoser import signals
from djoser import utils
from djoser.compat import get_user_email
from djoser.conf import settings
from djoser.email import ActivationEmail
from drf_spectacular.utils import extend_schema
from drf_spectacular.utils import extend_schema_view
from rest_framework import generics
from rest_framework import mixins
from rest_framework import permissions
from rest_framework import status
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound
from rest_framework.exceptions import ValidationError
from rest_framework.filters import OrderingFilter
from rest_framework.filters import SearchFilter
from rest_framework.parsers import FormParser
from rest_framework.parsers import JSONParser
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import SimpleRateThrottle
from rest_framework.throttling import UserRateThrottle
from rest_framework.viewsets import ModelViewSet
from rest_framework_simplejwt.authentication import AUTH_HEADER_TYPES
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.settings import api_settings

from core.applications.notification.audit.context import AuditContext
from core.applications.users import invitation_services
from core.applications.users.api.schemas import INVITATION_SCHEMA
from core.applications.users.api.schemas import ORGANIZATION_INVITATION_SCHEMA
from core.applications.users.api.serializers.organization_serializers import (
    AcceptInvitationSerializer,
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
    RoleOptionSerializer,
)
from core.applications.users.api.serializers.user_serializers import (
    CustomTokenObtainPairSerializer,
)
from core.applications.users.api.serializers.user_serializers import UserSerializer
from core.applications.users.auth_utils import build_auth_payload
from core.applications.users.models import Invitation
from core.applications.users.models import Membership
from core.applications.users.models import User
from core.applications.users.permissions import HasOrgPermission
from core.applications.users.permissions import OrganizationScopedMixin
from core.applications.users.token import default_token_generator
from core.helper.custom_exceptions import CustomError
from core.helper.enums import InvitationStatus
from core.helper.enums import PermissionCode

# setup logging
logger = logging.getLogger(__name__)


class AuthView(ModelViewSet):
    model = ActivationEmail


class TokenViewBase(generics.GenericAPIView):
    permission_classes = ()
    authentication_classes = ()
    parser_classes = [MultiPartParser, JSONParser]

    serializer_class = None
    _serializer_class = ""

    www_authenticate_realm = "api"

    def get_serializer_class(self):
        """
        If serializer_class is set, use it directly.
        Otherwise get the class from settings.
        """

        if self.serializer_class:
            return self.serializer_class
        try:
            return import_string(self._serializer_class)
        except ImportError as err:
            msg = f"Could not import serializer '{self._serializer_class}'"
            raise ImportError(msg) from err

    def get_authenticate_header(self, request):
        return f'{AUTH_HEADER_TYPES[0]} realm="{self.www_authenticate_realm}"'

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        try:
            serializer.is_valid(raise_exception=True)
        except TokenError as e:
            raise InvalidToken(e.args[0]) from e

        return Response(serializer.validated_data, status=status.HTTP_200_OK)


class TokenObtainPairView(TokenViewBase):
    """
    Takes a set of user credentials and returns an access and refresh JSON web
    token pair to prove the authentication of those credentials.
    """

    _serializer_class = api_settings.TOKEN_OBTAIN_SERIALIZER


token_obtain_pair = TokenObtainPairView.as_view()


class TokenObtainSlidingView(TokenViewBase):
    """
    Takes a set of user credentials and returns a sliding JSON web token to
    prove the authentication of those credentials.
    """

    _serializer_class = api_settings.SLIDING_TOKEN_OBTAIN_SERIALIZER


token_obtain_sliding = TokenObtainSlidingView.as_view()


class TokenRefreshSlidingView(TokenViewBase):
    """
    Takes a sliding JSON web token and returns a new, refreshed version if the
    token's refresh period has not expired.
    """

    _serializer_class = api_settings.SLIDING_TOKEN_REFRESH_SERIALIZER


token_refresh_sliding = TokenRefreshSlidingView.as_view()


class TokenRefreshView(TokenViewBase):
    """
    Takes a refresh type JSON web token and returns an access type JSON web
    token if the refresh token is valid.
    """

    _serializer_class = api_settings.TOKEN_REFRESH_SERIALIZER


token_refresh = TokenRefreshView.as_view()


class TokenVerifyView(TokenViewBase):
    """
    Takes a token and indicates if it is valid.  This view provides no
    information about a token's fitness for a particular use.
    """

    _serializer_class = api_settings.TOKEN_VERIFY_SERIALIZER


token_verify = TokenVerifyView.as_view()


class TokenBlacklistView(TokenViewBase):
    """
    Takes a token and blacklists it. Must be used with the
    `rest_framework_simplejwt.token_blacklist` app installed.
    """

    _serializer_class = api_settings.TOKEN_BLACKLIST_SERIALIZER


token_blacklist = TokenBlacklistView.as_view()


#  user





@extend_schema(tags=["User"])
class UserViewSet(ModelViewSet):
    serializer_class = settings.SERIALIZERS.user
    queryset = User.objects.all()
    permission_classes = [IsAuthenticated]
    token_generator = default_token_generator
    lookup_field = settings.USER_ID_FIELD
    parser_classes = [MultiPartParser, JSONParser, FormParser]

    def permission_denied(self, request, *args, **kwargs):
        if (
            settings.HIDE_USERS
            and request.user.is_authenticated
            and self.action in ["update", "partial_update", "list", "retrieve"]
        ):
            raise NotFound
        super().permission_denied(request, **kwargs)

    def get_queryset(self):
        user = self.request.user
        queryset = super().get_queryset()
        if settings.HIDE_USERS and self.action == "list" and not user.is_staff:
            queryset = queryset.filter(pk=user.pk)
        return queryset

    def get_permissions(self):
        """
        Defines the permission classes for the
        UserViewSet based on the current action.

        The permission classes are set according to the
        following actions:
            - create: settings.PERMISSIONS.user_create
            - activation: settings.PERMISSIONS.activation
            - resend_activation: settings.PERMISSIONS.password_reset
            - list: settings.PERMISSIONS.user_list
            - reset_password: settings.PERMISSIONS.password_reset
            - reset_password_confirm: settings.PERMISSIONS.password_reset_confirm
            - set_password: settings.PERMISSIONS.set_password
            - set_username: settings.PERMISSIONS.set_username
            - reset_username: settings.PERMISSIONS.username_reset
            - reset_username_confirm: settings.PERMISSIONS.username_reset_confirm
            - destroy or me with DELETE method: settings.PERMISSIONS.user_delete

        Returns the permission classes based on the current action.
        """
        if self.action == "create":
            self.permission_classes = settings.PERMISSIONS.user_create
        elif self.action == "activation":
            self.permission_classes = settings.PERMISSIONS.activation
        elif self.action == "resend_activation":
            self.permission_classes = settings.PERMISSIONS.password_reset
        elif self.action == "list":
            self.permission_classes = settings.PERMISSIONS.user_list
        elif self.action == "reset_password":
            self.permission_classes = settings.PERMISSIONS.password_reset
        elif self.action == "reset_password_confirm":
            self.permission_classes = settings.PERMISSIONS.password_reset_confirm
        elif self.action == "set_password":
            self.permission_classes = settings.PERMISSIONS.set_password
        elif self.action == "set_username":
            self.permission_classes = settings.PERMISSIONS.set_username
        elif self.action == "reset_username":
            self.permission_classes = settings.PERMISSIONS.username_reset
        elif self.action == "reset_username_confirm":
            self.permission_classes = settings.PERMISSIONS.username_reset_confirm
        elif self.action == "destroy" or (
            self.action == "me" and self.request and self.request.method == "DELETE"
        ):
            self.permission_classes = settings.PERMISSIONS.user_delete
        return super().get_permissions()

    def get_serializer_class(self):
        """
        Returns the serializer class to use in the view.

        This method returns different serializer classes based
        on the current action.
        The serializer classes are set according to the following actions:
            - create: settings.SERIALIZERS.user_create or
            settings.SERIALIZERS.user_create_password_retype
            - destroy: settings.SERIALIZERS.user_delete
            - activation: settings.SERIALIZERS.activation
            - resend_activation: settings.SERIALIZERS.password_reset
            - reset_password: settings.SERIALIZERS.password_reset
            - reset_password_confirm: settings.SERIALIZERS.password_reset_confirm
            or settings.SERIALIZERS.password_reset_confirm_retype
            - set_password: settings.SERIALIZERS.set_password or
            settings.SERIALIZERS.set_password_retype
            - set_username: settings.SERIALIZERS.set_username or
            settings.SERIALIZERS.set_username_retype
            - reset_username: settings.SERIALIZERS.username_reset
            - reset_username_confirm:
            settings.SERIALIZERS.username_reset_confirm or
            settings.SERIALIZERS.username_reset_confirm_retype
            - me: settings.SERIALIZERS.current_user

        Returns:
            The serializer class to use in the view.
        """
        if self.action == "create":
            if settings.USER_CREATE_PASSWORD_RETYPE:
                return settings.SERIALIZERS.user_create_password_retype
            return settings.SERIALIZERS.user_create
        if self.action == "destroy" or (
            self.action == "me" and self.request and self.request.method == "DELETE"
        ):
            return settings.SERIALIZERS.user_delete
        if self.action == "activation":
            return settings.SERIALIZERS.activation
        if self.action == "resend_activation" or self.action == "reset_password":
            return settings.SERIALIZERS.password_reset
        if self.action == "reset_password_confirm":
            if settings.PASSWORD_RESET_CONFIRM_RETYPE:
                return settings.SERIALIZERS.password_reset_confirm_retype
            return settings.SERIALIZERS.password_reset_confirm
        if self.action == "set_password":
            if settings.SET_PASSWORD_RETYPE:
                return settings.SERIALIZERS.set_password_retype
            return settings.SERIALIZERS.set_password
        if self.action == "set_username":
            if settings.SET_USERNAME_RETYPE:
                return settings.SERIALIZERS.set_username_retype
            return settings.SERIALIZERS.set_username
        if self.action == "reset_username":
            return settings.SERIALIZERS.username_reset
        if self.action == "reset_username_confirm":
            if settings.USERNAME_RESET_CONFIRM_RETYPE:
                return settings.SERIALIZERS.username_reset_confirm_retype
            return settings.SERIALIZERS.username_reset_confirm
        if self.action == "me":
            return settings.SERIALIZERS.current_user

        return self.serializer_class

    def get_instance(self):
        return self.request.user

    # @create_schema
    def perform_create(self, serializer, *args, **kwargs):
        """
        Handles the creation of a new user instance.

        Saves the user instance using the provided serializer
        and triggers the user_registered signal.

        Parameters:
            serializer (Serializer): The serializer instance
            used to create the user.
            *args: Variable length argument list.
            **kwargs: Arbitrary keyword arguments.

        Returns:
            None
        """
        user = serializer.save(*args, **kwargs)
        signals.user_registered.send(
            sender=self.__class__,
            user=user,
            request=self.request,
        )

        context = {"user": user}
        to = [get_user_email(user)]
        print("Sending email...")
        try:
            if settings.SEND_ACTIVATION_EMAIL:
                settings.EMAIL.activation(self.request, context).send(to)
            elif settings.SEND_CONFIRMATION_EMAIL:
                settings.EMAIL.confirmation(self.request, context).send(to)
            print("Email sent!")
        except SMTPRecipientsRefused as smtp_error:
            logger.error("SMTPRecipientsRefused: %s", smtp_error)
            raise CustomError.EmailSendError(
                "Unable to send email. Please contact support.",
            )

    # @action(
    #     detail=False,
    #     methods=["get"],
    #     url_path="email/(?P<email>.+)",
    #     url_name="get-by-email",
    # )
    # def get_by_email(self, request, email=None):
    #     """
    #     Custom endpoint to retrieve a user by email.
    #     Usage: /users/email/<email>/
    #     """
    #     try:
    #         user = User.objects.get(email=email)
    #     except User.DoesNotExist:
    #         raise NotFound("User with this email does not exist.")

    #     serializer = self.get_serializer(user)
    #     return Response(serializer.data)

    def perform_update(self, serializer, *args, **kwargs):
        """
        Handles the update of an existing user instance.

        Saves the user instance using the provided serializer
        and triggers the user_updated signal.

        Parameters:
            serializer (Serializer): The serializer instance
            used to update the user.
            *args: Variable length argument list.
            **kwargs: Arbitrary keyword arguments.

        Returns:
            None
        """
        super().perform_update(serializer, *args, **kwargs)
        user = serializer.instance
        signals.user_updated.send(
            sender=self.__class__,
            user=user,
            request=self.request,
        )

        # should we send activation email after update?
        if settings.SEND_ACTIVATION_EMAIL and not user.is_active:
            context = {"user": user}
            to = [get_user_email(user)]
            settings.EMAIL.activation(self.request, context).send(to)

    def destroy(self, request, *args, **kwargs):
        """
        Handles the deletion of an existing user instance.

        Parameters:
            request: The request object.
            *args: Variable length argument list.
            **kwargs: Arbitrary keyword arguments.

        Returns:
            A response with a status code of 204 (No Content)
            indicating the deletion was successful.
        """
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data)
        serializer.is_valid(raise_exception=True)

        if instance == request.user:
            utils.logout_user(self.request)
        self.perform_destroy(instance)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(["get", "put", "patch", "delete"], detail=False)
    def me(self, request, *args, **kwargs):
        self.get_object = self.get_instance
        if request.method == "GET":
            return self.retrieve(request, *args, **kwargs)
        if request.method == "PUT":
            return self.update(request, *args, **kwargs)
        if request.method == "PATCH":
            return self.partial_update(request, *args, **kwargs)
        if request.method == "DELETE":
            return self.destroy(request, *args, **kwargs)

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance)
        return Response(serializer.data)

    @action(["post"], detail=False)
    def activation(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.user
        user.is_active = True
        user.save()

        signals.user_activated.send(
            sender=self.__class__,
            user=user,
            request=self.request,
        )

        if settings.SEND_CONFIRMATION_EMAIL:
            context = {"user": user}
            to = [get_user_email(user)]
            settings.EMAIL.confirmation(self.request, context).send(to)

        data = build_auth_payload(user, CustomTokenObtainPairSerializer)
        return Response(data, status=status.HTTP_200_OK)

    @action(["post"], detail=False)
    def resend_activation(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.get_user(is_active=False)

        if not settings.SEND_ACTIVATION_EMAIL or not user:
            return Response(status=status.HTTP_400_BAD_REQUEST)

        context = {"user": user}
        to = [get_user_email(user)]
        settings.EMAIL.activation(self.request, context).send(to)

        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(["post"], detail=False)
    def set_password(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        self.request.user.set_password(serializer.data["new_password"])
        self.request.user.save()

        if settings.PASSWORD_CHANGED_EMAIL_CONFIRMATION:
            context = {"user": self.request.user}
            to = [get_user_email(self.request.user)]
            settings.EMAIL.password_changed_confirmation(self.request, context).send(to)

        if settings.LOGOUT_ON_PASSWORD_CHANGE:
            utils.logout_user(self.request)
        elif settings.CREATE_SESSION_ON_LOGIN:
            update_session_auth_hash(self.request, self.request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(["post"], detail=False)
    def reset_password(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.get_user()

        if user:
            context = {"user": user}
            to = [get_user_email(user)]
            settings.EMAIL.password_reset(self.request, context).send(to)

        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(["post"], detail=False)
    def reset_password_confirm(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        serializer.user.set_password(serializer.data["new_password"])
        if hasattr(serializer.user, "last_login"):
            serializer.user.last_login = now()
        serializer.user.save()

        if settings.PASSWORD_CHANGED_EMAIL_CONFIRMATION:
            context = {"user": serializer.user}
            to = [get_user_email(serializer.user)]
            settings.EMAIL.password_changed_confirmation(self.request, context).send(to)
            print("password reseted")
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(["post"], detail=False, url_path=f"set_{User.USERNAME_FIELD}")
    def set_username(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = self.request.user
        new_username = serializer.data["new_" + User.USERNAME_FIELD]

        setattr(user, User.USERNAME_FIELD, new_username)
        user.save()
        if settings.USERNAME_CHANGED_EMAIL_CONFIRMATION:
            context = {"user": user}
            to = [get_user_email(user)]
            settings.EMAIL.username_changed_confirmation(self.request, context).send(to)

    @action(["post"], detail=False, url_path=f"reset_{User.USERNAME_FIELD}")
    def reset_username(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.get_user()

        if user:
            context = {"user": user}
            to = [get_user_email(user)]
            settings.EMAIL.username_reset(self.request, context).send(to)

        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(["post"], detail=False, url_path=f"reset_{User.USERNAME_FIELD}_confirm")
    def reset_username_confirm(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        new_username = serializer.data["new_" + User.USERNAME_FIELD]

        setattr(serializer.user, User.USERNAME_FIELD, new_username)
        if hasattr(serializer.user, "last_login"):
            serializer.user.last_login = now()
        serializer.user.save()

        if settings.USERNAME_CHANGED_EMAIL_CONFIRMATION:
            context = {"user": serializer.user}
            to = [get_user_email(serializer.user)]
            settings.EMAIL.username_changed_confirmation(self.request, context).send(to)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(tags=["auth", "User Management"])
    @action(["get"], detail=False, authentication_classes=[JWTAuthentication])
    def logout(self, request, *args, **kwargs):
        if settings.TOKEN_MODEL:
            settings.TOKEN_MODEL.objects.filter(user=request.user).delete()
            user_logged_out.send(
                sender=request.user.__class__,
                request=request,
                user=request.user,
            )
        if settings.CREATE_SESSION_ON_LOGIN:
            logout(request)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(
        tags=["auth", "User Management"],
        # request=UserSerializer.PhoneMetadata,  # noqa: ERA001
        responses={status.HTTP_204_NO_CONTENT: None},
    )
    @action(["POST"], detail=False, authentication_classes=[JWTAuthentication])
    def metadatas(self, request: Request, *args, **kwargs):
        serializer = UserSerializer.PhoneMetadata(
            data=request.data,
            context=self.get_serializer_context(),
        )
        serializer.is_valid(raise_exception=True)
        serializer.update(request.user, serializer.validated_data)
        return Response(status=status.HTTP_204_NO_CONTENT)



class InvitationLookupThrottle(SimpleRateThrottle):
    """Per client IP, for everyone. AnonRateThrottle would skip logged-in callers."""

    scope = "invitation_lookup"

    def get_cache_key(self, request, view):
        return self.cache_format % {"scope": self.scope, "ident": self.get_ident(request)}


class InvitationAcceptThrottle(UserRateThrottle):
    scope = "invitation_accept"

@extend_schema_view(**INVITATION_SCHEMA)
class InvitationViewSet(viewsets.GenericViewSet):
    """Endpoints used by the invitee: preview an invitation, then accept it."""

    # Fail closed: a new action added here is authenticated unless it opts out.
    permission_classes = [permissions.IsAuthenticated]
    # Never expose a queryset here; every lookup goes through the service.
    queryset = Invitation.objects.none()
    serializer_class = AcceptInvitationSerializer

    @action(
        detail=False,
        methods=["get"],
        url_path=r"validate/(?P<token>[\w-]+)",
        url_name="validate",
        permission_classes=[permissions.AllowAny],
        # No auth: a stale or expired JWT must not break the public invite page.
        authentication_classes=[],
        throttle_classes=[InvitationLookupThrottle],
    )
    def validate_invite(self, request, token=None):
        """
        Public. Returns what the frontend needs to render the invitation
        before the invitee registers or logs in. Unknown, revoked and used
        tokens are indistinguishable by design.
        """
        invitation = invitation_services.get_invitation_preview(token)
        response = Response(InvitationPreviewSerializer(invitation).data)
        response["Cache-Control"] = "no-store"
        return response

    @action(
        detail=False,
        methods=["post"],
        url_path="accept",
        url_name="accept",
        throttle_classes=[InvitationAcceptThrottle],
    )
    def accept_invite(self, request):
        """
        Authenticated. Accepts an invitation; the account must have a verified
        email matching the invited address. Repeating a successful accept
        returns the same membership (idempotent, PRD §45).
        """
        serializer = AcceptInvitationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        membership = invitation_services.accept_invitation(
            token=serializer.validated_data["token"],
            user=request.user,
            audit_context=AuditContext.from_request(request),
        )
        return Response(InvitationAcceptedSerializer(membership).data, status=status.HTTP_200_OK)


# --------------------------------------------------------------------------- #
# Team side: organization-scoped, permission-gated.
# --------------------------------------------------------------------------- #
_ACTION_PERMISSIONS = {
    "list": HasOrgPermission(PermissionCode.VIEW_TEAM),
    "roles": HasOrgPermission(PermissionCode.INVITE_TEAM_MEMBER),  # populates the invite form
    "create": HasOrgPermission(PermissionCode.INVITE_TEAM_MEMBER),
    "resend": HasOrgPermission(PermissionCode.INVITE_TEAM_MEMBER),
    "revoke": HasOrgPermission(PermissionCode.REVOKE_INVITATION),
}
_DEFAULT_PERMISSION = HasOrgPermission(PermissionCode.VIEW_TEAM)

_STATUS_FILTERS = {
    "pending": lambda qs: qs.pending(),
    "expired": lambda qs: qs.expired(),  # derived from expires_at, not a stored status
    "accepted": lambda qs: qs.filter(status=InvitationStatus.ACCEPTED),
    "revoked": lambda qs: qs.filter(status=InvitationStatus.REVOKED),
}

_TEAM_TAGS = ["Team invitations"]


@extend_schema_view(**ORGANIZATION_INVITATION_SCHEMA )
class OrganizationInvitationViewSet(
    OrganizationScopedMixin,
    mixins.ListModelMixin,
    viewsets.GenericViewSet,
):
    """
    /organizations/{organization_id}/invitations/

    The organization comes from the URL and is verified against the caller's
    active membership by HasOrgPermission. Views never read it from the body.
    """

    queryset = Invitation.objects.none()  # real queryset: get_queryset()
    serializer_class = InvitationSerializer
    # Anything other than a UUID is a 404 at the router, never a database error.
    lookup_value_regex = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
    filter_backends = [SearchFilter, OrderingFilter]
    search_fields = ["name", "email"]
    ordering_fields = ["created_at", "expires_at", "name", "email"]
    ordering = ["-created_at"]

    # -- plumbing ---------------------------------------------------------- #
    def get_permissions(self):
        permission = _ACTION_PERMISSIONS.get(self.action, _DEFAULT_PERMISSION)
        return [permissions.IsAuthenticated(), permission()]

    def get_serializer_class(self):
        return {
            "create": InvitationCreateSerializer,
            "revoke": InvitationRevokeSerializer,
            "roles": RoleOptionSerializer,
        }.get(self.action, InvitationSerializer)

    def get_serializer_context(self):
        context = super().get_serializer_context()
        if not getattr(self, "swagger_fake_view", False):
            context.update(organization=self.organization, actor_membership=self.membership)
        return context

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Invitation.objects.none()

        queryset = Invitation.objects.for_organization(self.organization).with_related()

        requested = self.request.query_params.get("status")
        if requested:
            apply_filter = _STATUS_FILTERS.get(requested)
            if apply_filter is None:
                raise ValidationError({"status": [f"Choose one of: {', '.join(_STATUS_FILTERS)}."]})
            queryset = apply_filter(queryset)
        return queryset

    def _audit_context(self) -> AuditContext:
        return AuditContext.from_request(self.request)

    # -- endpoints --------------------------------------------------------- #
    # `list` comes from ListModelMixin; its schema is attached by extend_schema_view above.

    @action(detail=False, methods=["get"], url_path="roles", url_name="roles", pagination_class=None)
    def roles(self, request, *args, **kwargs):
        """Roles the caller may assign: feeds the "Assign Role" dropdown."""
        roles = invitation_services.assignable_roles(self.organization, self.membership)
        return Response(RoleOptionSerializer(roles, many=True).data)

    def create(self, request, *args, **kwargs):
        """Invite someone to this organization. The email is sent after the transaction commits."""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        issued = invitation_services.create_invitation(
            organization=self.organization,
            inviter=request.user,
            name=serializer.validated_data["name"],
            email=serializer.validated_data["email"],
            role=serializer.validated_data["role"],
            audit_context=self._audit_context(),
        )
        return Response(
            InvitationSerializer(issued.invitation, context=self.get_serializer_context()).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["post"], url_path="resend", url_name="resend")
    def resend(self, request, pk=None, *args, **kwargs):
        """Issue a fresh link (the old one stops working) and extend the expiry."""
        issued = invitation_services.resend_invitation(
            organization=self.organization,
            invitation_id=pk,
            actor=request.user,
            audit_context=self._audit_context(),
        )
        return Response(InvitationSerializer(issued.invitation, context=self.get_serializer_context()).data)

    @action(detail=True, methods=["post"], url_path="revoke", url_name="revoke")
    def revoke(self, request, pk=None, *args, **kwargs):
        """Cancel a pending invitation. The record is kept for history (PRD §44)."""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        invitation = invitation_services.revoke_invitation(
            organization=self.organization,
            invitation_id=pk,
            actor=request.user,
            reason=serializer.validated_data["reason"],
            audit_context=self._audit_context(),
        )
        return Response(InvitationSerializer(invitation, context=self.get_serializer_context()).data)
