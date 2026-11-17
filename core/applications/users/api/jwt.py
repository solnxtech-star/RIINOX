from django.db import transaction
from django.urls import re_path

from core.applications.users.api.views import users_views

urlpatterns = [
    # Not wrapped in the request transaction (ATOMIC_REQUESTS): a failed login ends in
    # an error response, and the rollback would discard the failed-login count/alert.
    re_path(
        r"^jwt/login/?$",
        transaction.non_atomic_requests(users_views.TokenObtainPairView.as_view()),
        name="jwt-create",
    ),
    re_path(r"^jwt/refresh/?", users_views.TokenRefreshView.as_view(), name="jwt-refresh"),
    re_path(r"^jwt/verify/?", users_views.TokenVerifyView.as_view(), name="jwt-verify"),
]
