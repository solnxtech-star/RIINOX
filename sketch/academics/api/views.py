from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from drf_spectacular.utils import extend_schema

from core.applications.users.permissions import IsPrincipalOrSchoolOwner
from core.applications.users.models import TeacherProfile
from core.applications.academics.models import TeachingAssignment

# Our serializers
from .serializers import (
    TeacherListSerializer,
    TeacherDetailSerializer,
    AdminAssignClassroomsSerializer,
    TeacherCreateTeachingAssignmentsSerializer,
    TeacherReassignTeachingAssignmentSerializer,
)


@extend_schema(tags=["Teacher Management"])
class TeacherViewSet(ModelViewSet):
    """
    Teacher Management ViewSet.

    Supports:
        ✔ List teachers (school-restricted)
        ✔ Retrieve teacher details
        ✔ Admin assigns classrooms to teacher
        ✔ Teacher assigns themselves subjects + classrooms
        ✔ Teacher updates/reassigns a single assignment
    """

    queryset = TeacherProfile.objects.select_related("user")
    permission_classes = [IsAuthenticated]

    # ---------------------------------------------------------
    # SELECT SERIALIZER BASED ON ACTION
    # ---------------------------------------------------------
    def get_serializer_class(self):
        if self.action == "list":
            return TeacherListSerializer
        if self.action == "retrieve":
            return TeacherDetailSerializer
        if self.action == "assign_classrooms":
            return AdminAssignClassroomsSerializer
        if self.action == "assign_teaching":
            return TeacherCreateTeachingAssignmentsSerializer
        if self.action == "reassign_teaching":
            return TeacherReassignTeachingAssignmentSerializer
        return TeacherDetailSerializer

    # ---------------------------------------------------------
    # MULTI-TENANCY → Only teachers from user’s school
    # ---------------------------------------------------------
    def get_queryset(self):
        school = self.request.user.school
        return (
            TeacherProfile.objects
            .filter(user__school=school)
            .select_related("user")
            .prefetch_related("classrooms")
        )

    # =========================================================
    # ADMIN → Assign Classrooms to Teacher
    # =========================================================
    @action(
        methods=["POST"],
        detail=True,
        url_path="assign-classrooms",
        permission_classes=[IsAuthenticated, IsPrincipalOrSchoolOwner],
    )
    @extend_schema(
        description="Assign multiple classrooms to a teacher (Admin Only)."
    )
    def assign_classrooms(self, request, pk=None):
        teacher = self.get_object()

        serializer = AdminAssignClassroomsSerializer(
            data=request.data,
            context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        serializer.save(teacher_profile=teacher)

        return Response({
            "message": "Classrooms assigned successfully.",
            "teacher": TeacherDetailSerializer(
                teacher, context={"request": request}
            ).data
        })

    # =========================================================
    # TEACHER → Create Teaching Assignments
    # =========================================================
    @action(
        methods=["POST"],
        detail=True,
        url_path="assign-teaching",
        permission_classes=[IsAuthenticated],
    )
    @extend_schema(
        description="Teacher assigns themselves to multiple classroom+subject combinations."
    )
    def assign_teaching(self, request, pk=None):
        teacher = self.get_object()

        # 🛡 Ensure teachers assign only themselves
        if request.user != teacher.user:
            return Response(
                {"detail": "You cannot assign teaching for another teacher."},
                status=403
            )

        serializer = TeacherCreateTeachingAssignmentsSerializer(
            data=request.data,
            context={"teacher": teacher}
        )
        serializer.is_valid(raise_exception=True)
        assignments = serializer.save()

        return Response({
            "message": "Teaching assignments created successfully.",
            "count": len(assignments),
            "assignments": [
                {
                    "id": str(a.id),
                    "classroom": str(a.classroom.id),
                    "subject": str(a.subject.id),
                }
                for a in assignments
            ],
        })

    # =========================================================
    # TEACHER → UPDATE / REASSIGN an Assignment
    # =========================================================
    @action(
        methods=["PATCH"],
        detail=True,
        url_path="reassign-teaching/(?P<assignment_id>[^/.]+)",
        permission_classes=[IsAuthenticated],
    )
    @extend_schema(
        description="Update an existing teaching assignment (change classroom or subject)."
    )
    def reassign_teaching(self, request, pk=None, assignment_id=None):
        teacher = self.get_object()

        # 🛡 Teachers can only modify their own assignments
        if request.user != teacher.user:
            return Response(
                {"detail": "You cannot modify teaching for another teacher."},
                status=403
            )

        try:
            assignment = TeachingAssignment.objects.get(
                id=assignment_id,
                teacher=teacher
            )
        except TeachingAssignment.DoesNotExist:
            return Response({"detail": "Teaching assignment not found."}, status=404)

        serializer = TeacherReassignTeachingAssignmentSerializer(
            data=request.data,
            context={"teacher": teacher, "assignment": assignment}
        )
        serializer.is_valid(raise_exception=True)
        updated_assignment = serializer.save()

        return Response({
            "message": "Teaching assignment updated successfully.",
            "assignment": {
                "id": str(updated_assignment.id),
                "classroom": str(updated_assignment.classroom.id),
                "subject": str(updated_assignment.subject.id),
            }
        })
