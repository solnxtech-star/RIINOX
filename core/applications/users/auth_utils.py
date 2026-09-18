# accounts/auth_utils.py
from django.contrib.auth.models import update_last_login
from django.contrib.auth.signals import user_logged_in
from rest_framework_simplejwt.settings import api_settings

from core.applications.users.api.serializers import UserSerializer


def build_auth_payload(user, serializer_class):
    """Same payload the login endpoint returns."""
    refresh = serializer_class.get_token(user)
    data = {
        "refresh": str(refresh),
        "access": str(refresh.access_token),
        "setup_info": UserSerializer.Info(instance=user).data,
        "registration_complete": user.is_active,
    }

    if api_settings.UPDATE_LAST_LOGIN:
        update_last_login(None, user)

    if not user.is_superuser:
        user_logged_in.send(
            sender=user.__class__,
            token=data["access"],
            user=user,
        )
    return data
