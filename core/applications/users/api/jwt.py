from django.urls import re_path

from core.applications.users.api.views import users_views

urlpatterns = [
    re_path(r"^jwt/login/?$", users_views.TokenObtainPairView.as_view(), name="jwt-create"),
    re_path(r"^jwt/refresh/?", users_views.TokenRefreshView.as_view(), name="jwt-refresh"),
    re_path(r"^jwt/verify/?", users_views.TokenVerifyView.as_view(), name="jwt-verify"),
]
