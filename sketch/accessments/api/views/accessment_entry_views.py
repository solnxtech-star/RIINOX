from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from django.shortcuts import get_object_or_404

from core.applications.academics.models import ClassRoom, TeachingAssignment
from core.applications.accessments.api.serializers.accessment_entry_serializers import (
    AssessmentEntryFormDataSerializer,
    AssessmentRecordSerializer,
    BulkAssessmentEntrySerializer,
)
from core.applications.accessments.models import AssessmentRecord, AssessmentType
from core.applications.users.models import StudentProfile

from core.applications.accessments.api.schemas import accessment_entry_schema
from drf_spectacular.utils import extend_schema


@accessment_entry_schema
@extend_schema(tags=["Assessment Management"])
class AssessmentRecordViewSet(viewsets.ModelViewSet):

    permission_classes = [IsAuthenticated]
    serializer_class = AssessmentRecordSerializer

    def get_queryset(self):
        school = self.request.user.school
        return (
            AssessmentRecord.objects.filter(student__school=school)
            .select_related(
                "student",
                "classroom_subject",                 # TeachingAssignment
                "classroom_subject__subject",
                "classroom_subject__classroom",
                "assessment_type",
            )
            .order_by("-created_at")
        )

    # --------------------------------------------------
    # 1. FORM DATA (load students + assessment types)
    # --------------------------------------------------
    @action(detail=False, methods=["post"], url_path="form-data")
    def form_data(self, request):

        serializer = AssessmentEntryFormDataSerializer(
            data=request.data, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)

        classroom = serializer.validated_data["class_room"]
        subject = serializer.validated_data["subject"]

        # Must match by TeachingAssignment
        classroom_subject = get_object_or_404(
            TeachingAssignment,
            classroom=classroom,
            subject=subject,
            teacher__school=request.user.school     # Multi-tenant safety
        )

        # Get students in the classroom
        students = StudentProfile.objects.filter(
            classroom=classroom,
            school=request.user.school
        )

        # Allowed assessment types for this school
        assessment_types = AssessmentType.objects.filter(
            policy__school=request.user.school
        )

        return Response({
            "classroom": {"id": classroom.id, "name": str(classroom)},
            "subject": {"id": subject.id, "name": subject.name},
            "students": [
                {"id": s.id, "name": s.user.name, "student_id": s.student_id}
                for s in students
            ],
            "assessment_types": [
                {"id": a.id, "name": a.name, "max_score": a.max_score, "count": a.count}
                for a in assessment_types
            ],
            "classroom_subject_id": classroom_subject.id,
        })

    # --------------------------------------------------
    # 2. BULK ENTRY ENDPOINT
    # --------------------------------------------------
    @action(detail=False, methods=["post"], url_path="bulk-entry")
    def bulk_entry(self, request):
        serializer = BulkAssessmentEntrySerializer(
            data=request.data, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)

        result = serializer.save()
        return Response(result, status=status.HTTP_201_CREATED)

    # --------------------------------------------------
    # 3. SINGLE CREATE OVERRIDE (unchanged)
    # --------------------------------------------------
    def create(self, request, *args, **kwargs):
        return super().create(request, *args, **kwargs)
