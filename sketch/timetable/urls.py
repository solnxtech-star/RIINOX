from django.urls import include
from django.urls import path
from rest_framework.routers import DefaultRouter

from core.applications.timetable.api.views import ClassScheduleViewSet
from core.applications.timetable.api.views import SubjectViewSet
from core.applications.timetable.api.views import TimeSlotViewSet
from core.applications.timetable.api.views import TimetableViewSet

app_name = "timetable"

router = DefaultRouter()
router.register(r"subjects", SubjectViewSet, basename="subject")
router.register(r"time-slots", TimeSlotViewSet, basename="timeslot")
router.register(r"class-schedules", ClassScheduleViewSet, basename="classschedule")
router.register(r"timetables", TimetableViewSet, basename="timetable")

urlpatterns = [
    path("", include(router.urls)),
]
