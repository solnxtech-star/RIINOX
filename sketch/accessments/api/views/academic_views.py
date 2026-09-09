from rest_framework import viewsets, status, filters
from drf_spectacular.utils import extend_schema, OpenApiResponse, OpenApiParameter
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.applications.academics.models import ClassRoom
from core.applications.accessments.models import AcademicSession, AcademicTerm
from core.applications.accessments.api.serializers.academic_serializers import (
    AcademicSessionSerializer,
    AcademicTermSerializer,
)
from core.applications.timetable.models import Subject
from core.applications.timetable.api.serializers import SubjectSerializer
from core.applications.users.permissions import IsPrincipalOrSchoolOwner
from drf_spectacular.utils import extend_schema, extend_schema_view, OpenApiParameter



@extend_schema_view(
    list=extend_schema(summary="List Academic Sessions", tags=["Academics"]),
    create=extend_schema(summary="Create an Academic Session", tags=["Academics"]),
    retrieve=extend_schema(summary="Retrieve Academic Session", tags=["Academics"]),
    update=extend_schema(summary="Update Academic Session", tags=["Academics"]),
    partial_update=extend_schema(summary="Patch Academic Session", tags=["Academics"]),
    destroy=extend_schema(summary="Delete Academic Session", tags=["Academics"])
)
class AcademicSessionViewSet(viewsets.ModelViewSet):
    serializer_class = AcademicSessionSerializer
    permission_classes = [IsAuthenticated, IsPrincipalOrSchoolOwner]

    def get_queryset(self):
        return AcademicSession.objects.filter(school=self.request.user.school)

    def perform_create(self, serializer):
        serializer.save(school=self.request.user.school)

    def perform_destroy(self, instance):
        instance.is_active = False
        instance.save()


@extend_schema_view(
    list=extend_schema(
        summary="List Terms",
        tags=["Academics"],
        parameters=[OpenApiParameter("session_id", str)]
    ),
    create=extend_schema(summary="Create Term", tags=["Academics"]),
    retrieve=extend_schema(summary="Retrieve Term", tags=["Academics"]),
    update=extend_schema(summary="Update Term", tags=["Academics"]),
    partial_update=extend_schema(summary="Patch Term", tags=["Academics"]),
    destroy=extend_schema(summary="Delete Term", tags=["Academics"]),
)
class AcademicTermViewSet(viewsets.ModelViewSet):
    serializer_class = AcademicTermSerializer
    permission_classes = [IsAuthenticated, IsPrincipalOrSchoolOwner]

    def get_queryset(self):
        qs = AcademicTerm.objects.filter(session__school=self.request.user.school)
        session_id = self.request.query_params.get("session_id")
        if session_id:
            qs = qs.filter(session_id=session_id)
        return qs

    def perform_destroy(self, instance):
        instance.is_active = False
        instance.save()


@extend_schema_view(
    list=extend_schema(
        summary="List Subjects",
        tags=["Academics"],
        parameters=[
            OpenApiParameter("search", str, description="Search by subject name")
        ],
    ),
    create=extend_schema(
        summary="Create Subject",
        description="Create a subject and optionally link it to one or more ClassRooms.",
        tags=["Academics"],
    ),
    retrieve=extend_schema(summary="Retrieve Subject", tags=["Academics"]),
    update=extend_schema(
        summary="Update Subject",
        description="Update subject details including assigned ClassRooms.",
        tags=["Academics"],
    ),
    partial_update=extend_schema(
        summary="Patch Subject",
        description="Partially update subject fields including class_rooms.",
        tags=["Academics"],
    ),
    destroy=extend_schema(
        summary="Delete Subject",
        description="Soft-delete the subject by marking it inactive.",
        tags=["Academics"],
    ),
)
class SubjectViewSet(viewsets.ModelViewSet):
    serializer_class = SubjectSerializer
    permission_classes = [IsAuthenticated, IsPrincipalOrSchoolOwner]
    filter_backends = [filters.SearchFilter]
    search_fields = ["name"]

    def get_queryset(self):
        return Subject.objects.filter(school=self.request.user.school)

    def perform_create(self, serializer):
        serializer.save(school=self.request.user.school)

    def perform_destroy(self, instance):
        instance.is_active = False
        instance.save()
