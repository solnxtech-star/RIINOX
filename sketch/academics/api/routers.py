from django.conf import settings
from rest_framework.routers import DefaultRouter, SimpleRouter

from core.applications.academics.api.views import TeacherViewSet


PREFIX = "academics"
API_VERSION = settings.API_VERSION

if settings.DEBUG:
    router = DefaultRouter()
else:
    router = SimpleRouter()

router.register("teachers", TeacherViewSet, basename="teachers")


app_name = f"{PREFIX}"
urlpatterns = router.urls
