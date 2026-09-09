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
from rest_framework import generics
from rest_framework import permissions
from rest_framework import status
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound
from rest_framework.parsers import FormParser
from rest_framework.parsers import JSONParser
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet
from rest_framework_simplejwt.authentication import AUTH_HEADER_TYPES
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.settings import api_settings

from core.applications.users.api.schemas import accept_invite_schema
from core.applications.users.api.schemas import create_organization_schema
from core.applications.users.api.schemas import delete_member_schema
from core.applications.users.api.schemas import destroy_organization_schema
from core.applications.users.api.schemas import invite_member_schema
from core.applications.users.api.schemas import list_members_schema
from core.applications.users.api.schemas import list_organization_schema
from core.applications.users.api.schemas import member_organization_schema
from core.applications.users.api.schemas import retrieve_member_schema
from core.applications.users.api.schemas import retrieve_organization_schema
from core.applications.users.api.schemas import update_member_schema
from core.applications.users.api.schemas import update_organization_schema
from core.applications.users.api.schemas import (
    validate_invite_schema,
    subscription_upgrade_schema,
)
from core.applications.users.models import Membership
from core.applications.users.models import Organization
from core.applications.users.models import User
from core.applications.users.token import default_token_generator
from core.helper.custom_exceptions import CustomError
from core.helper.permissions import IsOrganizationAdminOrOwner, IsOrganizationOwner

from .serializers import AcceptInvitationSerializer, SubscriptionUpgradeSerializer
from .serializers import InvitationCreateSerializer
from .serializers import MembershipSerializer
from .serializers import OrganizationCreateSerializer
from .serializers import OrganizationSerializer
from .serializers import OrganizationUpdateSerializer
from .serializers import UserSerializer

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

    @action(
        ["post"],
        detail=False,
    )
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

        return Response(status=status.HTTP_204_NO_CONTENT)

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


@extend_schema(tags=["Organizations"])
class OrganizationViewSet(ModelViewSet):
    """
    API endpoint for managing Organizations.

    - list: View all organizations the user belongs to.
    - retrieve: View a single organization with details.
    - create: Create a new organization (auto-links creator as Owner).
    - update/partial_update: Update organization settings (Admins + Owners only).
    - deactivate: Soft-deactivate organization (Owners only).
    - members: List organization members (Admins + Owners only).
    """

    queryset = Organization.objects.prefetch_related("memberships__user", "plan")
    permission_classes = [permissions.IsAuthenticated]

    def get_serializer_class(self):
        if self.action == "create":
            return OrganizationCreateSerializer
        if self.action in ["update", "partial_update"]:
            return OrganizationUpdateSerializer
        return OrganizationSerializer

    def get_queryset(self):
        """
        Users should only see organizations they belong to.
        """
        user = self.request.user
        return self.queryset.filter(memberships__user=user, memberships__is_active=True)

    def get_permissions(self):
        """
        Apply role-based permissions depending on the action.
        """
        if self.action in ["update", "partial_update", "members"]:
            return [permissions.IsAuthenticated(), IsOrganizationAdminOrOwner()]
        if self.action in ["destroy", "perform_destroy"]:
            return [permissions.IsAuthenticated(), IsOrganizationOwner()]
        return [permissions.IsAuthenticated()]

    @list_organization_schema
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @create_organization_schema
    def create(self, request, *args, **kwargs):
        return super().create(request, *args, **kwargs)

    @update_organization_schema
    def update(self, request, *args, **kwargs):
        return super().update(request, *args, **kwargs)

    @retrieve_organization_schema
    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)

    @destroy_organization_schema
    @action(detail=True, methods=["delete"], url_path="deactivate")
    def deactivate(self, request, pk=None):
        """
        Deactivate an organization (Owners only).
        """
        org = self.get_object()
        self.check_object_permissions(request, org)
        org.is_active = False
        org.save()
        return Response({"detail": f"Organization '{org.name}' deactivated."})

    @member_organization_schema
    @action(detail=True, methods=["get"])
    def members(self, request, pk=None):
        """
        List members of an organization.
        Example: GET /api/organizations/{id}/members/
        """
        org = self.get_object()
        self.check_object_permissions(request, org)
        memberships = org.memberships.select_related("user").all()
        data = [
            {"user": m.user.email, "role": m.role, "active": m.is_active}
            for m in memberships
        ]
        return Response(data)


@extend_schema(
    tags=["Invitations"],
)
class InvitationViewSet(viewsets.GenericViewSet):
    """
    ViewSet for handling invitations.
    Includes endpoints to validate and accept invitations.
    """

    queryset = Membership.objects.all()

    @validate_invite_schema
    @action(
        detail=False,
        methods=["get"],
        url_path="validate/(?P<token>[0-9a-f-]+)",
        permission_classes=[permissions.AllowAny],

    )
    def validate_invite(self, request, token=None):
        """
        Public endpoint to check if an invitation token is valid.
        This is used by the frontend to display invitation details
        before the user registers or logs in.
        """
        try:
            membership = Membership.objects.get(invite_token=token, accepted=False)
        except Membership.DoesNotExist:
            return Response({"detail": "Invalid or expired invitation."}, status=400)

        if membership.expires_at < timezone.now():
            return Response({"detail": "Invitation expired."}, status=400)

        return Response(
            {
                "organization": membership.organization.name,
                "role": membership.role,
                "invited_email": membership.invited_email,
                "expires_at": membership.expires_at,
            },
        )

    @accept_invite_schema
    @action(
        detail=False,
        methods=["post"],
        url_path="accept",
        permission_classes=[permissions.IsAuthenticated],
    )
    def accept_invite(self, request):
        """
        Accept an invitation to join an organization.
        Requires the user to be signed up and logged in
        with the same email address that received the invite.
        """
        serializer = AcceptInvitationSerializer(
            data=request.data,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        membership = serializer.save()

        return Response(
            {
                "detail": "Invitation accepted successfully.",
                "organization": membership.organization.name,
                "role": membership.role,
            },
            status=status.HTTP_200_OK,
        )


@extend_schema(
    tags=["Memberships"],
)
class MembershipViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing memberships within organizations.
    Only admins can invite new members.
    """

    queryset = Membership.objects.all()
    permission_classes = [permissions.IsAuthenticated]

    def get_serializer_class(self):
        if self.action == "invite":
            return InvitationCreateSerializer
        return MembershipSerializer

    @list_members_schema
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @retrieve_member_schema
    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)

    @update_member_schema
    def update(self, request, *args, **kwargs):
        return super().update(request, *args, **kwargs)

    @delete_member_schema
    def destroy(self, request, *args, **kwargs):
        return super().destroy(request, *args, **kwargs)

    @invite_member_schema
    @action(
        detail=False,
        methods=["post"],
        url_path="invite",
        permission_classes=[permissions.IsAuthenticated, IsOrganizationAdminOrOwner],
    )
    def invite(self, request):
        """
        Invite a new member to the organization.
        Only users with Admin role may perform this action.
        """
        serializer = self.get_serializer(
            data=request.data,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()

        return Response(
            {"detail": "Invitation sent successfully."},
            status=status.HTTP_201_CREATED,
        )


class SubscriptionUpgradeView(generics.GenericAPIView):
    """
    API endpoint for upgrading an organization's subscription.
    Only Owners can perform this action.
    """

    serializer_class = SubscriptionUpgradeSerializer
    permission_classes = [IsAuthenticated, IsOrganizationOwner]

    def get_object(self):
        """
        Get the organization from the URL.
        """
        return Organization.objects.get(pk=self.kwargs["org_id"])

    @subscription_upgrade_schema
    def post(self, request, *args, **kwargs):
        organization = self.get_object()
        self.check_object_permissions(request, organization)

        serializer = self.get_serializer(
            data=request.data,
            context={"request": request, "organization": organization},
        )
        serializer.is_valid(raise_exception=True)
        result = serializer.save()

        return Response(
            {
                "message": "Payment initiated. Complete payment to upgrade plan.",
                "organization": result["organization"].name,
                "new_plan": result["plan"].name,
                "payment_reference": result["payment"].reference,
                "payment_status": result["payment"].status,
            },
            status=status.HTTP_201_CREATED,
        )
