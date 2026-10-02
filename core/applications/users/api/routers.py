
from django.conf import settings
from rest_framework.routers import DefaultRouter
from rest_framework.routers import SimpleRouter

from core.applications.users.api.views.organization_views import BusinessTypeViewSet
from core.applications.users.api.views.organization_views import MembershipViewSet
from core.applications.users.api.views.organization_views import MetadataViewSet
from core.applications.users.api.views.organization_views import OrganizationViewSet
from core.applications.users.api.views.users_views import InvitationViewSet
from core.applications.users.api.views.users_views import OrganizationInvitationViewSet
from core.applications.users.api.views.users_views import UserViewSet
from core.helper.patterns import UUID_RE

PREFIX = "users"

API_VERSION = settings.API_VERSION

router = DefaultRouter() if settings.DEBUG else SimpleRouter()

router.register("users", UserViewSet, basename="users")
router.register("organizations", OrganizationViewSet, basename="organizations")
router.register("memberships", MembershipViewSet, basename="memberships")
router.register("meta-data", MetadataViewSet, basename="metadata")
router.register("business-type", BusinessTypeViewSet, basename="business-type")

# Invitations, public side: the invitee holds a token and may not have an account yet.
router.register("invitations", InvitationViewSet, basename="invitations")

# Invitations, team side: the organization id is in the URL and is verified against
# the caller's membership by HasOrgPermission. It is never read from the request body.
router.register(
    rf"organizations/(?P<organization_id>{UUID_RE})/invitations",
    OrganizationInvitationViewSet,
    basename="organization-invitations",
)


app_name = f"{PREFIX}"
urlpatterns = router.urls
