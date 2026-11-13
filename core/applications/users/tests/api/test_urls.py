from django.urls import resolve
from django.urls import reverse

from core.applications.users.models import User


def test_user_detail(user: User):
    assert (
        reverse("users-api:users-detail", kwargs={"id": user.pk}) == f"/api/users/{user.pk}/"
    )
    assert resolve(f"/api/users/{user.pk}/").view_name == "users-api:users-detail"


def test_user_list():
    assert reverse("users-api:users-list") == "/api/users/"
    assert resolve("/api/users/").view_name == "users-api:users-list"


def test_user_me():
    assert reverse("users-api:users-me") == "/api/users/me/"
    assert resolve("/api/users/me/").view_name == "users-api:users-me"
