from django.conf import settings
from rest_framework.routers import DefaultRouter
from rest_framework.routers import SimpleRouter

from core.applications.users.api.views.organization_views import MembershipViewSet, BusinessTypeViewSet
from core.applications.users.api.views.organization_views import MetadataViewSet
from core.applications.users.api.views.organization_views import OrganizationViewSet
from core.applications.users.api.views.users_views import InvitationViewSet
from core.applications.users.api.views.users_views import UserViewSet

PREFIX = "users"

API_VERSION = settings.API_VERSION

router = DefaultRouter() if settings.DEBUG else SimpleRouter()

router.register("users", UserViewSet, basename="users")
router.register("organizations", OrganizationViewSet, basename="organizations")
router.register("invitations", InvitationViewSet, basename="invitations")
router.register("memberships", MembershipViewSet, basename="memberships")
router.register("meta-data", MetadataViewSet, basename="metadata")
router.register("business-type", BusinessTypeViewSet, basename="business-type")



app_name = f"{PREFIX}"
urlpatterns = router.urls
