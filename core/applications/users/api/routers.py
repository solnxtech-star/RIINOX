from django.conf import settings
from rest_framework.routers import DefaultRouter
from rest_framework.routers import SimpleRouter

from core.applications.users.api.views import InvitationViewSet
from core.applications.users.api.views import MembershipViewSet
from core.applications.users.api.views import OrganizationViewSet
from core.applications.users.api.views import UserViewSet

PREFIX = "users"

API_VERSION = settings.API_VERSION

if settings.DEBUG:
    router = DefaultRouter()
else:
    router = SimpleRouter()

router.register("users", UserViewSet, basename="users")
router.register("organizations", OrganizationViewSet, basename="organizations")
router.register("invitations", InvitationViewSet, basename="invitations")
router.register("memberships", MembershipViewSet, basename="memberships")


app_name = f"{PREFIX}"
urlpatterns = router.urls
