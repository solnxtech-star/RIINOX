from django.conf import settings

from rest_framework.routers import DefaultRouter, SimpleRouter
from core.applications.accessments.api.views.academic_views import (
    AcademicSessionViewSet,
    AcademicTermViewSet,
    SubjectViewSet,
)
from core.applications.accessments.api.views.accessment_entry_views import AssessmentRecordViewSet
from core.applications.accessments.api.views.accessment_views import (
    AssessmentPolicyViewSet,
    AssessmentTypeViewSet,
)
from core.applications.accessments.api.views.grades_views import GradeScaleViewSet


PREFIX = "accessments"
API_VERSION = settings.API_VERSION

if settings.DEBUG:
    router = DefaultRouter()
else:
    router = SimpleRouter()


# ====== FIXED ORDER: Most specific FIRST, generic LAST ======
# 1. Specific paths FIRST
router.register("sessions", AcademicSessionViewSet, basename="academic-sessions")
router.register("terms", AcademicTermViewSet, basename="academic-terms")
router.register("subjects", SubjectViewSet, basename="subjects")
router.register("grade-scales", GradeScaleViewSet, basename="grade-scales")
router.register(
    "assessment-policies", AssessmentPolicyViewSet, basename="assessment-policies"
)
router.register("assessment-types", AssessmentTypeViewSet, basename="assessment-types")
router.register(
    "assessment-records", AssessmentRecordViewSet, basename="assessment-records"
)

# =============================================================

app_name = f"{PREFIX}"
urlpatterns = router.urls
