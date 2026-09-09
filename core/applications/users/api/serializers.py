import contextlib
from datetime import timezone
from typing import Literal

from django.contrib.auth import authenticate
from django.contrib.auth import user_logged_in
from django.contrib.auth.models import update_last_login
from django.contrib.auth.password_validation import validate_password
from django.core import exceptions as django_exceptions
from djoser.compat import get_user_email
from djoser.conf import settings
from django.conf import settings as main_setting
from djoser.serializers import UserCreateSerializer
from rest_framework import serializers
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.exceptions import PermissionDenied
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from pathlib import Path
from rest_framework_simplejwt.settings import api_settings

from core.applications.payment.models import Payment
from core.applications.users.models import DocumentTemplate, Membership
from core.applications.users.models import Organization
from core.applications.users.models import Plan
from core.applications.users.models import User
from core.applications.users.token import default_token_generator
from core.helper.custom_exceptions import CustomError
from core.helper.enums import PaymentStatus, UsersRole
from core.helper.interface import BaseModelNoDefs
from core.helper.utils import send_invitation_email
from django.core.files import File


class OSNameSchema(BaseModelNoDefs):
    Android: Literal["Android"] | None = None
    iOS: Literal["iOS", "iPadOS"] | None = None  # noqa: N815
    web: Literal["iOS", "Windows", "Android"] | None = None


class ModelNameSchema(BaseModelNoDefs):
    Android: str | None = None
    iOS: str | None = None  # noqa: N815
    web: str | None = None


class OSVersionSchema(BaseModelNoDefs):
    Android: str | None = None
    iOS: str | None = None  # noqa: N815
    web: str | None = None


class UserDeviceInfoSchema(BaseModelNoDefs):
    osName: Literal["Android", "android", "iOS", "ios", "web", "Web"] | None = None
    modelName: str | None = None  # noqa: N815
    osVersion: str | None = None  # noqa: N815


class UserMetadataSchema(BaseModelNoDefs):
    push_notification_id: str | None
    device_info: UserDeviceInfoSchema | None


class UserSerializer(serializers.ModelSerializer[User]):
    class Meta:
        model = User
        fields = ["name", "url"]

        extra_kwargs = {
            "url": {"view_name": "api:user-detail", "lookup_field": "pk"},
        }


class UserSerializer:
    class AddOrRetrieveDevice(serializers.ModelSerializer):
        class Meta:
            model = User
            fields = ("email",)

    class Update(serializers.ModelSerializer):
        class Meta:
            model = User
            fields = ("email",)

    class Info(serializers.ModelSerializer):
        class Meta:
            model = User
            fields = (
                "id",
                "email",
                "name",
            )


class CustomUserCreateSerializer(UserCreateSerializer):
    re_password = serializers.CharField(
        style={"input_type": "password"},
        required=True,
        write_only=True,
    )

    def validate(self, attrs):
        re_password = attrs.pop("re_password")
        attrs = super().validate(attrs)
        password = attrs.get("password")

        if password != re_password:
            msg = "The passwords entered do not match."
            raise CustomError.BadRequest(
                {"re_password": msg},
            )
        return attrs

    class Meta(UserCreateSerializer.Meta):
        fields = (
            "id",
            "name",
            "email",
            "password",
            "re_password",
        )
        extra_kwargs = {
            "re_password": {"write_only": True},
        }


class CustomUserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = [
            "id",
            "name",
            "is_active",
            "email",
        ]


class GetUser(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = (
            "id",
            "email",
            "name",
            "is_active",
        )


class EmailAndTokenSerializer(serializers.Serializer):
    email = serializers.EmailField()
    token = serializers.CharField()

    default_error_messages = {
        "invalid_token": "The token may have expired or is invalid.",
        "invalid_email": "No user found with that email. Create an account or try another email.",  # noqa: E501
    }

    def validate(self, attrs):
        validated_data = super().validate(attrs)

        # uid validation have to be here, because validate_<field_name>
        # doesn't work with modelserializer
        try:
            email = self.initial_data.get("email", "")
            self.user = User.objects.get(email=email)
        except (User.DoesNotExist, ValueError, TypeError, OverflowError) as e:
            key_error = "invalid_email"
            raise CustomError.BadRequest(
                {"email": self.error_messages[key_error]},
                code=key_error,
            ) from e

        is_token_valid = default_token_generator.check_token(
            self.user,
            self.initial_data.get("token", ""),
        )
        if is_token_valid:
            return validated_data
        key_error = "invalid_token"
        raise CustomError.BadRequest(
            {"token": self.error_messages[key_error]},
            code=key_error,
        )


class PasswordSerializer(serializers.Serializer):
    new_password = serializers.CharField(style={"input_type": "password"})

    def validate(self, attrs):
        user = getattr(self, "user", None) or self.context["request"].user
        # why assert? There are ValidationError / fail everywhere
        assert user is not None

        try:
            validate_password(attrs["new_password"], user)
        except django_exceptions.ValidationError as e:
            raise CustomError.BadRequest({"new_password": e.messages[0]})  # noqa: B904
        return super().validate(attrs)


class PasswordRetypeSerializer(PasswordSerializer):
    re_new_password = serializers.CharField(style={"input_type": "password"})

    default_error_messages = {
        "password_mismatch": settings.CONSTANTS.messages.PASSWORD_MISMATCH_ERROR,
    }

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if attrs["new_password"] == attrs["re_new_password"]:
            return attrs
        return self.fail("password_mismatch")


class UsernameSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = (settings.LOGIN_FIELD,)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.username_field = settings.LOGIN_FIELD
        self._default_username_field = User.USERNAME_FIELD
        self.fields[f"new_{self.username_field}"] = self.fields.pop(self.username_field)

    def save(self, **kwargs):
        if self.username_field != self._default_username_field:
            kwargs[User.USERNAME_FIELD] = self.validated_data.get(
                f"new_{self.username_field}",
            )
        return super().save(**kwargs)


class UsernameRetypeSerializer(UsernameSerializer):
    default_error_messages = {
        "username_mismatch": settings.CONSTANTS.messages.USERNAME_MISMATCH_ERROR.format(
            settings.LOGIN_FIELD,
        ),
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["re_new_" + settings.LOGIN_FIELD] = serializers.CharField()

    def validate(self, attrs):
        attrs = super().validate(attrs)
        new_username = attrs[settings.LOGIN_FIELD]
        if new_username != attrs[f"re_new_{settings.LOGIN_FIELD}"]:
            return self.fail("username_mismatch")
        return attrs


class ActivationSerializer(EmailAndTokenSerializer):
    """
    Serializer for user activation.
    It validates the token and checks if the user is active.
    If the user is active, it raises a PermissionDenied exception.
    If the token is invalid, it raises a BadRequest exception.
    If the user is not active, it returns the validated data."""

    default_error_messages = {
        "stale_token": settings.CONSTANTS.messages.STALE_TOKEN_ERROR,
    }

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if not self.user.is_active:
            return attrs
        raise PermissionDenied(self.error_messages["stale_token"])


class PasswordResetConfirmSerializer(EmailAndTokenSerializer, PasswordSerializer):
    pass


class PasswordResetConfirmRetypeSerializer(
    EmailAndTokenSerializer,
    PasswordRetypeSerializer,
):
    pass


class UsernameResetConfirmSerializer(EmailAndTokenSerializer, UsernameSerializer):
    pass


class UsernameResetConfirmRetypeSerializer(
    EmailAndTokenSerializer,
    UsernameRetypeSerializer,
):
    pass


class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    email = serializers.EmailField()
    password = serializers.CharField()

    def get_setup_info(self, user: User):
        return {"user_info": user.accounts_dict, "is_verified": user.is_verified}

    def validate(self, attrs):
        authenticate_kwargs = {
            self.username_field: attrs[self.username_field],
            "password": attrs["password"],
        }
        with contextlib.suppress(KeyError):
            authenticate_kwargs["request"] = self.context["request"]

        self.user: User = authenticate(**authenticate_kwargs)
        if not self.user:
            if user := User.objects.filter(email=attrs["email"]).first():
                if not user.is_active:
                    context = {"user": user}
                    to = [get_user_email(user)]
                    settings.EMAIL.activation(self.context["request"], context).send(to)
                    msg = "Your account is not yet verified, kindly check yur email and proceed to verification"  # noqa: E501
                    raise PermissionDenied(
                        msg,
                    )
                if not api_settings.USER_AUTHENTICATION_RULE(self.user):
                    raise AuthenticationFailed(
                        detail="Login failed. Please check your email and password and try again.",  # noqa: E501
                    )

        data = super().validate(attrs)
        refresh = self.get_token(self.user)
        data["refresh"] = str(refresh)
        data["access"] = str(refresh.access_token)
        data["setup_info"] = None
        data["registration_complete"] = None
        data["setup_info"] = UserSerializer.Info(instance=self.user).data
        data["registration_complete"] = all([self.user.is_active])
        if api_settings.UPDATE_LAST_LOGIN:
            update_last_login(None, self.user)
        if not self.user.is_superuser:
            user_logged_in.send(
                sender=self.user.__class__,
                token=data["access"],
                user=self.user,
            )
        return data


class PlanSerializer(serializers.ModelSerializer):
    """
    Serializer for the subscription Plan linked to an organization.
    """

    class Meta:
        model = Plan
        fields = ["id", "name", "description", "price", "is_active"]


class TemplateField(serializers.PrimaryKeyRelatedField):
    """
    Custom field that allows either:
    - A UUID of an existing DocumentTemplate
    - The string "default" (handled in serializer.create)
    - null (if no template provided)
    """

    def to_internal_value(self, data):
        if isinstance(data, str) and data.lower() == "default":
            return "default"  # Pass literal "default" to serializer
        if data is None:
            return None
        return super().to_internal_value(data)


class OrganizationSerializer(serializers.ModelSerializer):
    """
    Base serializer for Organization.
    Includes plan and related members.
    """

    plan = PlanSerializer(read_only=True)
    members = serializers.SerializerMethodField()

    class Meta:
        model = Organization
        fields = [
            "id",
            "name",
            "type",
            "domain",
            "plan",
            "is_active",
            "created_at",
            "updated_at",
            "members",
        ]
        read_only_fields = ["id", "created_at", "updated_at", "members"]

    def get_members(self, obj):
        """
        Retrieve all active members of the organization along with their roles.
        """
        memberships = Membership.objects.filter(organization=obj, is_active=True)
        return [
            {
                "user": UserSerializer(m.user).data,
                "role": m.role,
            }
            for m in memberships
        ]


class OrganizationCreateSerializer(serializers.ModelSerializer):
    """
    Serializer for creating a new Organization.
    Handles invoice and receipt templates, including auto-provision of default templates.
    """

    invoice_template = TemplateField(
        queryset=DocumentTemplate.objects.filter(
            template_type="invoice", is_active=True
        ),
        required=False,
        allow_null=True,
    )
    receipt_template = TemplateField(
        queryset=DocumentTemplate.objects.filter(
            template_type="receipt", is_active=True
        ),
        required=False,
        allow_null=True,
    )

    class Meta:
        model = Organization
        fields = [
            "name",
            "type",
            "domain",
            "logo",
            "invoice_template",
            "receipt_template",
            "header_text",
            "footer_text",
        ]

    def create(self, validated_data):
        request = self.context.get("request")

        # Enforce Free plan
        free_plan = Plan.objects.filter(name__iexact="Free").first()
        if not free_plan:
            raise serializers.ValidationError(
                {"plan": "Default Free plan is not configured. Please contact support."}
            )

        invoice_template = validated_data.pop("invoice_template", None)
        receipt_template = validated_data.pop("receipt_template", None)

        # Create organization
        organization = Organization.objects.create(plan=free_plan, **validated_data)

        # Handle invoice template
        if invoice_template in (None, "default"):
            invoice_template = self._create_default_template(
                organization, "invoice", "default_invoice.html"
            )

        # Handle receipt template
        if receipt_template in (None, "default"):
            receipt_template = self._create_default_template(
                organization, "receipt", "default_receipt.html"
            )

        # Assign templates
        organization.invoice_template = invoice_template
        organization.receipt_template = receipt_template
        organization.save()

        # Assign creator as OWNER
        if request and request.user.is_authenticated:
            Membership.objects.create(
                user=request.user,
                organization=organization,
                role=UsersRole.OWNER,
            )

        return organization

    def _create_default_template(self, organization, template_type, filename):
        """
        Create a DocumentTemplate by copying a bundled default file into media storage.
        """
        default_path = Path(main_setting.APPS_DIR) / "templates" / "documents" / filename
        if not default_path.exists():
            raise serializers.ValidationError(
                {
                    f"{template_type}_template": f"Default {template_type} template file is missing."
                }
            )

        with default_path.open("rb") as f:
            return DocumentTemplate.objects.create(
                name=f"{organization.name} - Default {template_type.title()}",
                template_type=template_type,
                description=f"Auto-provisioned default {template_type} template.",
                file=File(f, name=filename),
                is_active=True,
            )


class OrganizationUpdateSerializer(serializers.ModelSerializer):
    """
    Serializer for updating Organization settings and customization.
    Restricts template fields to only valid active templates of the right type.
    """

    invoice_template = serializers.PrimaryKeyRelatedField(
        queryset=DocumentTemplate.objects.filter(
            template_type="invoice", is_active=True
        ),
        required=False,
        allow_null=True,
    )
    receipt_template = serializers.PrimaryKeyRelatedField(
        queryset=DocumentTemplate.objects.filter(
            template_type="receipt", is_active=True
        ),
        required=False,
        allow_null=True,
    )

    class Meta:
        model = Organization
        fields = [
            "name",
            "domain",
            "logo",
            "invoice_template",
            "receipt_template",
            "header_text",
            "footer_text",
        ]
        read_only_fields = ["is_active"]


class OrganizationMemberSerializer(serializers.Serializer):
    """
    Serializer for organization members in the members endpoint.
    """

    user = serializers.EmailField(help_text="Email address of the member")
    role = serializers.CharField(
        help_text="Role of the user in the organization (e.g., Admin, Client)",
    )
    active = serializers.BooleanField(
        help_text="Whether the membership is currently active",
    )
    joined_at = serializers.DateTimeField(
        help_text="Timestamp when the user joined the organization",
    )


class MembershipSerializer(serializers.ModelSerializer):
    """
    Serializer for listing memberships (users in an organization).
    Includes user email or invited email depending on acceptance.
    """

    user_email = serializers.SerializerMethodField()

    class Meta:
        model = Membership
        fields = ["id", "user_email", "role", "is_active", "accepted"]

    def get_user_email(self, obj):
        if obj.user:
            return obj.user.email
        return obj.invited_email


class InvitationCreateSerializer(serializers.ModelSerializer):
    """
    Serializer for creating (sending) invitations.
    """

    class Meta:
        model = Membership
        fields = ["id", "invited_email", "role", "organization"]

    def validate(self, attrs):
        org = attrs["organization"]
        email = attrs["invited_email"]

        if Membership.objects.filter(
            organization=org,
            invited_email=email,
            accepted=False,
        ).exists():
            raise serializers.ValidationError(
                "This email is already invited to the organization.",
            )
        return attrs

    def create(self, validated_data):
        membership = Membership.objects.create(**validated_data)
        send_invitation_email(membership)
        return membership


class MembershipSerializer(serializers.ModelSerializer):
    """
    Default serializer for Membership details (read-only).
    Displays the user if attached, otherwise shows invited email.
    """

    user = serializers.StringRelatedField(read_only=True)
    organization = serializers.StringRelatedField(read_only=True)

    class Meta:
        model = Membership
        fields = [
            "id",
            "user",
            "invited_email",
            "organization",
            "role",
            "is_active",
            "accepted",
            "expires_at",
        ]
        read_only_fields = fields


class AcceptInvitationSerializer(serializers.Serializer):
    """
    Serializer for accepting an organization invitation.

    Workflow:
    - User receives an invitation email with a token.
    - User must be authenticated to accept.
    - The authenticated user's email must match the invited_email.
    - If the user does not exist yet, they must register first
      with the same email before accepting.
    """

    token = serializers.UUIDField(
        help_text="Unique invitation token sent to the invited user.",
    )

    def validate(self, attrs):
        """
        Validate the invitation token and check business rules:
        - Token exists and is not already accepted.
        - Token has not expired.
        - User is authenticated.
        - User’s email matches the invited email.
        """
        token = attrs["token"]

        # Find the membership record by invite token
        try:
            membership = Membership.objects.get(invite_token=token, accepted=False)
        except Membership.DoesNotExist:
            raise serializers.ValidationError("Invalid or expired invitation token.")

        # Check if the invite is expired
        if membership.expires_at < timezone.now():
            raise serializers.ValidationError("This invitation has expired.")

        request = self.context["request"]

        # Ensure the user is logged in
        if not request.user.is_authenticated:
            raise serializers.ValidationError(
                "You must be logged in to accept an invitation.",
            )

        # Ensure the logged-in user matches the invited email
        if membership.invited_email.lower() != request.user.email.lower():
            raise serializers.ValidationError(
                "This invitation was not sent to your email address.",
            )

        # Attach membership to validated data for use in `save()`
        attrs["membership"] = membership
        return attrs

    def save(self, **kwargs):
        """
        Bind the membership to the authenticated user and mark as accepted.
        """
        membership = self.validated_data["membership"]
        user = self.context["request"].user

        membership.user = user
        membership.accepted = True
        membership.is_active = True
        membership.save()

        return membership


class SubscriptionUpgradeSerializer(serializers.Serializer):
    """
    Serializer to handle subscription upgrades.
    - Requires a target plan.
    - Creates a pending payment record.
    """

    plan_id = serializers.PrimaryKeyRelatedField(
        queryset=Plan.objects.filter(is_active=True),
        required=True,
        help_text="ID of the plan to upgrade to.",
    )
    payment_method = serializers.CharField(
        required=True, help_text="Payment method (e.g., paystack, nowpayments)."
    )

    def validate_plan_id(self, plan):
        """Ensure user is not already on the selected plan."""
        organization = self.context["organization"]
        if organization.plan_id == plan.id:
            raise serializers.ValidationError("Organization is already on this plan.")
        return plan

    def create(self, validated_data):
        """
        Create a pending payment for the plan upgrade.
        """
        organization = self.context["organization"]
        user = self.context["request"].user
        plan = validated_data["plan_id"]
        method = validated_data["payment_method"]

        # Create a pending payment record
        payment = Payment.objects.create(
            user=user,
            amount=plan.price,  # assuming Plan.price is a DecimalField in future
            currency="NGN",
            method=method,
            status=PaymentStatus.PENDING,
            reference=f"SUB-{organization.id}-{plan.id}-{user.id}",  #  unique reference
            description=f"Upgrade {organization.name} to {plan.name} plan",
        )

        return {
            "organization": organization,
            "plan": plan,
            "payment": payment,
        }
