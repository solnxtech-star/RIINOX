import contextlib
import re
from datetime import timezone
from pathlib import Path
from typing import Literal

from django.conf import settings as main_setting
from django.contrib.auth import authenticate
from django.contrib.auth import user_logged_in
from django.contrib.auth.models import update_last_login
from django.contrib.auth.password_validation import validate_password
from django.core import exceptions as django_exceptions
from django.core.files import File
from django.utils.encoding import force_str
from django.utils.http import urlsafe_base64_decode
from django.utils.translation import gettext_lazy as _
from djoser.compat import get_user_email
from djoser.conf import settings
from djoser.serializers import UserCreateSerializer
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.exceptions import PermissionDenied
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.settings import api_settings

from core.applications.users.models import User
from core.applications.users.token import default_token_generator
from core.helper.custom_exceptions import CustomError
from core.helper.interface import BaseModelNoDefs


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

class UidAndTokenSerializer(serializers.Serializer):
    """
    Validates uid + token for password reset.
    """
    uid = serializers.CharField()
    token = serializers.CharField()

    default_error_messages = {
        "invalid_token": "The token may have expired or is invalid.",
        "invalid_uid": "Invalid activation link.",
    }

    def validate(self, attrs):
        validated_data = super().validate(attrs)

        try:
            uid = force_str(urlsafe_base64_decode(self.initial_data.get("uid", "")))
            self.user = User.objects.get(pk=uid)
        except (User.DoesNotExist, ValueError, TypeError, OverflowError) as e:
            raise CustomError.BadRequest(
                {"uid": self.error_messages["invalid_uid"]},
                code="invalid_uid",
            ) from e

        is_token_valid = default_token_generator.check_token(
            self.user,
            self.initial_data.get("token", ""),
        )
        if is_token_valid:
            return validated_data

        raise CustomError.BadRequest(
            {"token": self.error_messages["invalid_token"]},
            code="invalid_token",
        )
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
