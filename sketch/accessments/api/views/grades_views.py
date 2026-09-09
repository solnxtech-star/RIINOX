from typing import List

from django.db import transaction
from django.shortcuts import get_object_or_404

from rest_framework import viewsets, status, serializers
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from drf_spectacular.utils import extend_schema, OpenApiResponse, OpenApiParameter

from core.applications.accessments.api.serializers.grading_serializers import (
    DefaultGradingSystemSerializer,
    GradeScaleBulkCreateSerializer,
    GradeScaleSerializer,
)
from core.applications.accessments.models import GradeScale
from core.applications.accessments.permissions import IsPrincipalOrSchoolOwner


@extend_schema(
    tags=["Grade Scales"],
    description="API for managing grading scales for the authenticated user's school.",
)
class GradeScaleViewSet(viewsets.ModelViewSet):
    """
    GradeScaleViewSet
    - Principals & School Owners: full CRUD + bulk operations
    - Teachers: read-only
    """

    serializer_class = GradeScaleSerializer
    permission_classes = [IsAuthenticated, IsPrincipalOrSchoolOwner]

    def get_queryset(self):
        """Limit all queries to the authenticated user's school"""
        user = self.request.user
        qs = GradeScale.objects.filter(school=user.school).order_by(
            "-max_score", "order"
        )
        return qs

    def perform_create(self, serializer):
        """Ensure created GradeScale is bound to request.user.school."""
        serializer.save(school=self.request.user.school)

    def perform_update(self, serializer):
        """Prevent changing the school via update; ensure it remains user's school."""
        serializer.save(school=self.request.user.school)

    # ---------------------------
    # Bulk create
    # ---------------------------
    @extend_schema(
        request=GradeScaleBulkCreateSerializer,
        responses={201: OpenApiResponse(description="Bulk-created grade scales")},
        summary="Bulk create grading scales (atomic).",
        description="Deactivate existing active scales and create the provided scales in one transaction.",
    )
    @action(detail=False, methods=["post"], url_path="bulk-create")
    @transaction.atomic
    def bulk_create(self, request):
        serializer = GradeScaleBulkCreateSerializer(
            data=request.data, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        result = serializer.create(
            serializer.validated_data
        )  # returns {'scales': [...]}

        # Serialize created models for response
        created_scales = result.get("scales", [])
        out = GradeScaleSerializer(
            created_scales, many=True, context={"request": request}
        ).data
        return Response(
            {"message": f"Created {len(created_scales)} grade scales.", "scales": out},
            status=status.HTTP_201_CREATED,
        )

    # ---------------------------
    # Apply default system
    # ---------------------------
    @extend_schema(
        request=DefaultGradingSystemSerializer,
        responses={200: OpenApiResponse(description="Applied default grading system")},
        summary="Apply a pre-configured grading system (standard/extended/nigerian).",
    )
    @action(detail=False, methods=["post"], url_path="apply-default")
    @transaction.atomic
    def apply_default(self, request):
        """
        Accepts { system_name: 'standard'|'extended'|'nigerian'|'custom' }.
        For non-custom systems, will deploy the default scales as a bulk-create.
        """
        serializer = DefaultGradingSystemSerializer(
            data=request.data, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)

        system_name = serializer.validated_data["system_name"]
        scales = serializer.get_default_scales(system_name)

        # Reuse bulk creation logic but bypass external serializer call
        school = request.user.school
        GradeScale.objects.filter(school=school, is_active=True).update(is_active=False)

        created = []
        for idx, s in enumerate(scales):
            gs = GradeScale.objects.create(
                school=school,
                grade=s["grade"],
                display_name=s.get("display_name"),
                min_score=s["min_score"],
                max_score=s["max_score"],
                point=s["point"],
                remark=s.get("remark", ""),
                is_honors=s.get("is_honors", False),
                order=idx,
                is_active=True,
            )
            created.append(gs)

        out = GradeScaleSerializer(
            created, many=True, context={"request": request}
        ).data
        return Response(
            {"message": f"Applied '{system_name}' system", "scales": out},
            status=status.HTTP_200_OK,
        )

    # ---------------------------
    # Activate / Deactivate
    # ---------------------------
    @extend_schema(
        responses={200: OpenApiResponse(description="Activated grade scale")},
        summary="Activate a grade scale",
    )
    @action(detail=True, methods=["post"], url_path="activate")
    @transaction.atomic
    def activate(self, request, pk=None):
        obj = get_object_or_404(self.get_queryset(), pk=pk)
        obj.is_active = True
        obj.save(update_fields=["is_active"])
        return Response(
            {"message": f"Activated grade '{obj.grade}'."}, status=status.HTTP_200_OK
        )

    @extend_schema(
        responses={200: OpenApiResponse(description="Deactivated grade scale")},
        summary="Deactivate a grade scale",
    )
    @action(detail=True, methods=["post"], url_path="deactivate")
    @transaction.atomic
    def deactivate(self, request, pk=None):
        obj = get_object_or_404(self.get_queryset(), pk=pk)
        obj.is_active = False
        obj.save(update_fields=["is_active"])
        return Response(
            {"message": f"Deactivated grade '{obj.grade}'."}, status=status.HTTP_200_OK
        )

    # ---------------------------
    # Reset (clear all active scales)
    # ---------------------------
    @extend_schema(
        responses={204: OpenApiResponse(description="Reset grading system")},
        summary="Reset grading system (deactivate all active scales)",
        description="Deactivate all active GradeScales for the school. This does not delete records (soft reset).",
    )
    @action(detail=False, methods=["post"], url_path="reset")
    @transaction.atomic
    def reset(self, request):
        school = request.user.school
        GradeScale.objects.filter(school=school, is_active=True).update(is_active=False)
        return Response(
            {"message": "Deactivated all active grade scales."},
            status=status.HTTP_204_NO_CONTENT,
        )

    # ---------------------------
    # Reorder
    # ---------------------------
    @extend_schema(
        request=serializers.ListField(child=serializers.IntegerField()),
        responses={200: OpenApiResponse(description="Reordered grade scales")},
        summary="Reorder grade scales",
        description="Accepts a list of grade scale IDs in desired sorted order (highest grade first).",
    )
    @action(detail=False, methods=["post"], url_path="reorder")
    @transaction.atomic
    def reorder(self, request):
        """
        Expects body: {"order": [<grade_scale_id_1>, <grade_scale_id_2>, ...]}
        Will set 'order' indices accordingly for the given school's grade scales.
        """
        order_list = request.data.get("order", None)
        if not isinstance(order_list, list):
            return Response(
                {"detail": "Provide 'order' as a list of GradeScale IDs."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Only update the grade scales that belong to the user's school
        qs = GradeScale.objects.filter(school=request.user.school, id__in=order_list)
        existing_ids = set(qs.values_list("id", flat=True))
        missing = [i for i in order_list if i not in existing_ids]
        if missing:
            return Response(
                {
                    "detail": f"The following IDs do not belong to your school: {missing}"
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Update order fields
        for idx, gs_id in enumerate(order_list):
            GradeScale.objects.filter(pk=gs_id, school=request.user.school).update(
                order=idx
            )

        return Response(
            {"message": "Reordered grade scales."}, status=status.HTTP_200_OK
        )
