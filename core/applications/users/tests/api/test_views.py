import pytest
from rest_framework.test import APIRequestFactory

from core.applications.users.api.views import UserViewSet
from core.applications.users.models import User


class TestUserViewSet:
    @pytest.fixture
    def api_rf(self) -> APIRequestFactory:
        return APIRequestFactory()

    def _view(self, user: User, api_rf: APIRequestFactory, action: str) -> UserViewSet:
        request = api_rf.get("/fake-url/")
        request.user = user
        view = UserViewSet(action=action, request=request, format_kwarg=None)
        return view

    def test_get_queryset(self, user: User, api_rf: APIRequestFactory):
        view = self._view(user, api_rf, action="list")

        assert user in view.get_queryset()

    def test_me(self, user: User, api_rf: APIRequestFactory):
        view = self._view(user, api_rf, action="me")

        response = view.me(view.request)  # type: ignore[call-arg, arg-type, misc]

        assert response.data["id"] == user.pk
        assert response.data["email"] == user.email
        assert response.data["name"] == user.name
