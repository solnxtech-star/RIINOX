from django.db.models import Prefetch
from drf_spectacular.utils import extend_schema
from drf_spectacular.utils import extend_schema_view
from rest_framework import status
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.applications.timetable.api.serializers import ClassScheduleListSerializer
from core.applications.timetable.api.serializers import ClassScheduleSerializer
from core.applications.timetable.api.serializers import StudentTimetableSerializer
from core.applications.timetable.api.serializers import SubjectSerializer
from core.applications.timetable.api.serializers import TimeSlotSerializer
from core.applications.timetable.api.serializers import TimetableSerializer
from core.applications.timetable.models import ClassSchedule
from core.applications.timetable.models import Subject
from core.applications.timetable.models import TimeSlot
from core.applications.timetable.models import Timetable
from core.applications.timetable.api.schemas import subject_schema
from core.applications.users.permissions import CanManageSubjects
from core.helper.enums import UserRole


@extend_schema_view(**subject_schema)
class SubjectViewSet(viewsets.ModelViewSet):
    """
    Manage academic subjects for a specific school.

    Students: Read-only
    Teachers: Create + Update
    Admins: Full CRUD
    """

    serializer_class = SubjectSerializer
    permission_classes = [CanManageSubjects]

    def get_queryset(self):
        user = self.request.user

        # Students don't have adminprofile.school but can still view active subjects
        school = getattr(user, "adminprofile", None)
        if school:
            school = school.school

        if school:
            return Subject.objects.filter(
                school=school,
                is_active=True,
            ).order_by("name")

        return Subject.objects.none()

    # ---------- Response Formatting ----------
    def success(self, message, data=None, status_code=status.HTTP_200_OK):
        return Response(
            {"success": True, "message": message, "data": data},
            status=status_code,
        )

    # ---------- CRUD ----------
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        subject = serializer.save()
        return self.success(
            "Subject created successfully",
            SubjectSerializer(subject).data,
            status.HTTP_201_CREATED,
        )

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        instance = self.get_object()

        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        subject = serializer.save()

        return self.success(
            "Subject updated successfully",
            SubjectSerializer(subject).data,
        )

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        instance.is_active = False
        instance.save()
        return self.success("Subject archived successfully")

@extend_schema_view(
    list=extend_schema(description="List all time slots"),
    retrieve=extend_schema(description="Get a specific time slot"),
    create=extend_schema(description="Create a new time slot (Admin only)"),
    update=extend_schema(description="Update a time slot (Admin only)"),
    partial_update=extend_schema(
        description="Partially update a time slot (Admin only)",
    ),
    destroy=extend_schema(description="Delete a time slot (Admin only)"),
)
class TimeSlotViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing time slots.

    Students/Teachers: Read-only access
    Admins: Full CRUD access
    """

    queryset = TimeSlot.objects.all()
    serializer_class = TimeSlotSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return TimeSlot.objects.all().order_by("order", "start_time")

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        serializer = self.get_serializer(queryset, many=True)
        return Response(
            {
                "success": True,
                "message": "Time slots retrieved successfully",
                "data": serializer.data,
            },
        )

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance)
        return Response(
            {
                "success": True,
                "message": "Time slot retrieved successfully",
                "data": serializer.data,
            },
        )

    def create(self, request, *args, **kwargs):
        if request.user.role != UserRole.ADMIN:
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied("Only admins can create time slots.")

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)

        return Response(
            {
                "success": True,
                "message": "Time slot created successfully",
                "data": serializer.data,
            },
            status=status.HTTP_201_CREATED,
        )

    def perform_update(self, serializer):
        if self.request.user.role != UserRole.ADMIN:
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied("Only admins can update time slots.")
        serializer.save()

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)

        return Response(
            {
                "success": True,
                "message": "Time slot updated successfully",
                "data": serializer.data,
            },
        )

    def perform_destroy(self, instance):
        if self.request.user.role != UserRole.ADMIN:
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied("Only admins can delete time slots.")
        instance.delete()

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        self.perform_destroy(instance)
        return Response(
            {
                "success": True,
                "message": "Time slot deleted successfully",
                "data": None,
            },
            status=status.HTTP_200_OK,
        )


@extend_schema_view(
    list=extend_schema(description="List all class schedules"),
    retrieve=extend_schema(description="Get a specific class schedule"),
    create=extend_schema(description="Create a new class schedule (Admin only)"),
    update=extend_schema(description="Update a class schedule (Admin only)"),
    partial_update=extend_schema(
        description="Partially update a class schedule (Admin only)",
    ),
    destroy=extend_schema(description="Delete a class schedule (Admin only)"),
)
class ClassScheduleViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing class schedules.

    Students: Can view their class schedules only
    Teachers: Can view all schedules
    Admins: Full CRUD access
    """

    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        queryset = ClassSchedule.objects.select_related(
            "subject",
            "teacher",
            "time_slot",
        ).filter(is_active=True)

        # Students can only see their class schedules
        if user.role == UserRole.STUDENT:
            if hasattr(user, "studentprofile"):
                student_class = user.studentprofile.current_class
                queryset = queryset.filter(academic_class=student_class)
            else:
                # If student profile doesn't exist, return empty queryset
                return ClassSchedule.objects.none()

        # Teachers can see all schedules (or optionally filter to their own)
        # Admins can see all schedules

        return queryset.order_by("academic_class", "day_of_week", "time_slot__order")

    def get_serializer_class(self):
        if self.action == "list":
            return ClassScheduleListSerializer
        return ClassScheduleSerializer

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        serializer = self.get_serializer(queryset, many=True)
        return Response(
            {
                "success": True,
                "message": "Class schedules retrieved successfully",
                "data": serializer.data,
            },
        )

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance)
        return Response(
            {
                "success": True,
                "message": "Class schedule retrieved successfully",
                "data": serializer.data,
            },
        )

    def create(self, request, *args, **kwargs):
        if request.user.role != UserRole.ADMIN:
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied("Only admins can create class schedules.")

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)

        return Response(
            {
                "success": True,
                "message": "Class schedule created successfully",
                "data": serializer.data,
            },
            status=status.HTTP_201_CREATED,
        )

    def perform_update(self, serializer):
        if self.request.user.role != UserRole.ADMIN:
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied("Only admins can update class schedules.")
        serializer.save()

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)

        return Response(
            {
                "success": True,
                "message": "Class schedule updated successfully",
                "data": serializer.data,
            },
        )

    def perform_destroy(self, instance):
        if self.request.user.role != UserRole.ADMIN:
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied("Only admins can delete class schedules.")
        instance.delete()

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        self.perform_destroy(instance)
        return Response(
            {
                "success": True,
                "message": "Class schedule deleted successfully",
                "data": None,
            },
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        description="Get schedules filtered by day of week",
        parameters=[
            {
                "name": "day",
                "required": True,
                "type": "string",
                "description": "Day of week (e.g., MONDAY, TUESDAY)",
            },
        ],
    )
    @action(detail=False, methods=["get"])
    def by_day(self, request):
        """Get schedules for a specific day"""
        day = request.query_params.get("day", "").upper()
        if not day:
            return Response(
                {
                    "success": False,
                    "message": "Day parameter is required",
                    "data": None,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        queryset = self.get_queryset().filter(day_of_week=day)
        serializer = ClassScheduleListSerializer(queryset, many=True)
        return Response(
            {
                "success": True,
                "message": f"Schedules for {day} retrieved successfully",
                "data": serializer.data,
            },
        )

    @extend_schema(
        description="Get schedules filtered by class",
        parameters=[
            {
                "name": "class",
                "required": True,
                "type": "string",
                "description": "Academic class (e.g., Primary 1, JSS1)",
            },
        ],
    )
    @action(detail=False, methods=["get"])
    def by_class(self, request):
        """Get schedules for a specific class"""
        academic_class = request.query_params.get("class", "")
        if not academic_class:
            return Response(
                {
                    "success": False,
                    "message": "Class parameter is required",
                    "data": None,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Admins and teachers can view any class
        # Students can only view their own class
        if request.user.role == UserRole.STUDENT:
            if hasattr(request.user, "studentprofile"):
                if request.user.studentprofile.current_class != academic_class:
                    from rest_framework.exceptions import PermissionDenied

                    raise PermissionDenied("You can only view your own class schedule.")

        queryset = self.get_queryset().filter(academic_class=academic_class)
        serializer = ClassScheduleListSerializer(queryset, many=True)
        return Response(
            {
                "success": True,
                "message": f"Schedules for {academic_class} retrieved successfully",
                "data": serializer.data,
            },
        )


@extend_schema_view(
    list=extend_schema(description="List all timetables"),
    retrieve=extend_schema(description="Get a specific timetable"),
    create=extend_schema(description="Create a new timetable (Admin only)"),
    update=extend_schema(description="Update a timetable (Admin only)"),
    partial_update=extend_schema(
        description="Partially update a timetable (Admin only)",
    ),
    destroy=extend_schema(description="Delete a timetable (Admin only)"),
)
class TimetableViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing complete timetables.

    Students: Can view active timetable for their class
    Teachers: Can view all timetables
    Admins: Full CRUD access
    """

    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = Timetable.objects.prefetch_related(
            Prefetch(
                "schedules",
                queryset=ClassSchedule.objects.select_related(
                    "subject",
                    "teacher",
                    "time_slot",
                ).filter(is_active=True),
            ),
        )

        # Students only see active timetables
        if self.request.user.role == UserRole.STUDENT:
            queryset = queryset.filter(is_active=True)

        return queryset.order_by("-start_date")

    def get_serializer_class(self):
        # Use student-specific serializer for students
        if self.request.user.role == UserRole.STUDENT and self.action in [
            "retrieve",
            "list",
            "my_timetable",
        ]:
            return StudentTimetableSerializer
        return TimetableSerializer

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["request"] = self.request
        return context

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        serializer = self.get_serializer(queryset, many=True)
        return Response(
            {
                "success": True,
                "message": "Timetables retrieved successfully",
                "data": serializer.data,
            },
        )

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance)
        return Response(
            {
                "success": True,
                "message": "Timetable retrieved successfully",
                "data": serializer.data,
            },
        )

    def create(self, request, *args, **kwargs):
        if request.user.role != UserRole.ADMIN:
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied("Only admins can create timetables.")

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)

        return Response(
            {
                "success": True,
                "message": "Timetable created successfully",
                "data": serializer.data,
            },
            status=status.HTTP_201_CREATED,
        )

    def perform_update(self, serializer):
        if self.request.user.role != UserRole.ADMIN:
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied("Only admins can update timetables.")
        serializer.save()

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)

        return Response(
            {
                "success": True,
                "message": "Timetable updated successfully",
                "data": serializer.data,
            },
        )

    def perform_destroy(self, instance):
        if self.request.user.role != UserRole.ADMIN:
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied("Only admins can delete timetables.")
        instance.delete()

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        self.perform_destroy(instance)
        return Response(
            {
                "success": True,
                "message": "Timetable deleted successfully",
                "data": None,
            },
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        description="Get the current active timetable for the logged-in student",
        responses={200: StudentTimetableSerializer},
    )
    @action(detail=False, methods=["get"])
    def my_timetable(self, request):
        """Get current active timetable for the logged-in student"""
        if request.user.role != UserRole.STUDENT:
            return Response(
                {
                    "success": False,
                    "message": "This endpoint is only for students",
                    "data": None,
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        if not hasattr(request.user, "studentprofile"):
            return Response(
                {
                    "success": False,
                    "message": "Student profile not found",
                    "data": None,
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        # Get active timetable
        timetable = (
            Timetable.objects.filter(is_active=True)
            .prefetch_related(
                Prefetch(
                    "schedules",
                    queryset=ClassSchedule.objects.select_related(
                        "subject",
                        "teacher",
                        "time_slot",
                    ).filter(is_active=True),
                ),
            )
            .first()
        )

        if not timetable:
            return Response(
                {
                    "success": False,
                    "message": "No active timetable found",
                    "data": None,
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = StudentTimetableSerializer(
            timetable,
            context={"request": request},
        )
        return Response(
            {
                "success": True,
                "message": "Your timetable retrieved successfully",
                "data": serializer.data,
            },
        )

    @extend_schema(
        description="Get the currently active timetable",
        responses={200: TimetableSerializer},
    )
    @action(detail=False, methods=["get"])
    def active(self, request):
        """Get the currently active timetable"""
        timetable = (
            Timetable.objects.filter(is_active=True)
            .prefetch_related(
                "schedules__subject",
                "schedules__teacher",
                "schedules__time_slot",
            )
            .first()
        )

        if not timetable:
            return Response(
                {
                    "success": False,
                    "message": "No active timetable found",
                    "data": None,
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = self.get_serializer(timetable)
        return Response(
            {
                "success": True,
                "message": "Active timetable retrieved successfully",
                "data": serializer.data,
            },
        )
