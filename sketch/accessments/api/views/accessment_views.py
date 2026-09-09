from typing import Optional

from django.shortcuts import get_object_or_404
from django.db import transaction, models as dj_models

from rest_framework import viewsets, status, filters
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated, SAFE_METHODS
from rest_framework.response import Response

from drf_spectacular.utils import (
    extend_schema,
    extend_schema_view,
    OpenApiResponse,
    OpenApiParameter,
)


from core.applications.accessments.api.serializers.accessment_serializers import AssessmentPolicySerializer, AssessmentTypeSerializer, DefaultAssessmentPolicySerializer
from core.applications.accessments.models import AssessmentPolicy, AssessmentType
from core.applications.accessments.permissions import IsPrincipalOrSchoolOwnerForPolicies
from core.applications.users.models import User




# ---------------------------
# AssessmentPolicyViewSet
# ---------------------------
@extend_schema_view(
    list=extend_schema(
        summary="List assessment policies",
        description="List assessment policies for the authenticated user's school. Teachers may view."
    ),
    retrieve=extend_schema(
        summary="Retrieve an assessment policy",
        description="Get details for a single AssessmentPolicy including nested AssessmentTypes."
    ),
    create=extend_schema(
        summary="Create an assessment policy",
        description="Create an AssessmentPolicy (admins only). Ensure ca_weight + exam_weight = 100."
    ),
    update=extend_schema(
        summary="Update an assessment policy",
        description="Update an AssessmentPolicy (admins only)."
    ),
    partial_update=extend_schema(
        summary="Partial update assessment policy",
        description="Partial update (admins only)."
    ),
    destroy=extend_schema(
        summary="Delete an assessment policy",
        description="Delete (hard delete) an AssessmentPolicy (admins only)."
    ),
)
class AssessmentPolicyViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing AssessmentPolicy objects.

    - Principals & School Owners: full CRUD + apply-default (create preconfigured policies)
    - Teachers: read-only access
    """

    permission_classes = [IsAuthenticated, IsPrincipalOrSchoolOwnerForPolicies]
    serializer_class = AssessmentPolicySerializer
    filter_backends = [filters.OrderingFilter, filters.SearchFilter]
    ordering_fields = ["created_at", "name", "term"]
    search_fields = ["name", "term__name"]

    def get_queryset(self):
        """Only policies for the authenticated user's school"""
        user = self.request.user
        qs = AssessmentPolicy.objects.filter(school=user.school).select_related("term", "school")
        return qs

    def perform_create(self, serializer):
        """Bind policy to user's school and ensure transactional safety"""
        serializer.save(school=self.request.user.school)

    def perform_update(self, serializer):
        """Ensure policy remains associated with user's school"""
        serializer.save(school=self.request.user.school)

    # ---------------------------
    # Custom: Apply preconfigured default policy
    # ---------------------------
    @extend_schema(
        request=DefaultAssessmentPolicySerializer,
        responses={201: OpenApiResponse(description="Created default assessment policy")},
        summary="Create default assessment policy",
        description="Creates a pre-configured AssessmentPolicy and its AssessmentTypes (atomic)."
    )
    @action(detail=False, methods=["post"], url_path="apply-default")
    @transaction.atomic
    def apply_default(self, request):
        """
        Body: { term: <term_id>, config_type: 'standard_60_40'|'half_term'|'detailed', policy_name: '...' }
        Creates a policy with default types; deactivates existing active policy for that term.
        """
        serializer = DefaultAssessmentPolicySerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)

        policy = serializer.create(serializer.validated_data)
        out = AssessmentPolicySerializer(policy, context={"request": request}).data
        return Response({"message": "Default policy created", "policy": out}, status=status.HTTP_201_CREATED)

    # ---------------------------
    # Custom: Get active policy for a given term
    # ---------------------------
    @extend_schema(
        parameters=[
            OpenApiParameter("term", description="AcademicTerm ID to fetch active policy for", required=False, type=int)
        ],
        responses={200: AssessmentPolicySerializer},
        summary="Get active policy for a term",
        description="If term is provided as query param (?term=<id>) returns the active policy for that term; otherwise returns active policies."
    )
    @action(detail=False, methods=["get"], url_path="active-for-term")
    def active_for_term(self, request):
        term_id = request.query_params.get("term", None)
        school = request.user.school
        if term_id:
            policy = AssessmentPolicy.objects.filter(school=school, term_id=term_id, is_active=True).first()
            if not policy:
                return Response({"detail": "No active policy for the provided term."}, status=status.HTTP_404_NOT_FOUND)
            data = AssessmentPolicySerializer(policy, context={"request": request}).data
            return Response(data, status=status.HTTP_200_OK)
        else:
            policies = AssessmentPolicy.objects.filter(school=school, is_active=True)
            data = AssessmentPolicySerializer(policies, many=True, context={"request": request}).data
            return Response(data, status=status.HTTP_200_OK)


# ---------------------------
# AssessmentTypeViewSet
# ---------------------------
@extend_schema_view(
    list=extend_schema(
        summary="List assessment types",
        description="List AssessmentType objects. Use ?policy=<policy_id> to filter types for a policy."
    ),
    retrieve=extend_schema(
        summary="Retrieve an assessment type",
        description="Get a single AssessmentType details."
    ),
    create=extend_schema(
        summary="Create an assessment type",
        description="Create an AssessmentType under a policy (admins only). Ensures total policy weight <= 100%."
    ),
    update=extend_schema(
        summary="Update an assessment type",
        description="Update AssessmentType (admins only)."
    ),
    destroy=extend_schema(
        summary="Delete an assessment type",
        description="Delete an AssessmentType (admins only)."
    ),
)
class AssessmentTypeViewSet(viewsets.ModelViewSet):
    """
    CRUD operations for AssessmentType.

    - Filter by ?policy=<policy_id>
    - Principals/School Owners: full access
    - Teachers: read-only
    """

    permission_classes = [IsAuthenticated, IsPrincipalOrSchoolOwnerForPolicies]
    serializer_class = AssessmentTypeSerializer
    filter_backends = [filters.OrderingFilter, filters.SearchFilter]
    ordering_fields = ["order", "name", "created_at"]
    search_fields = ["name", "category"]

    def get_queryset(self):
        user = self.request.user
        qs = AssessmentType.objects.filter(policy__school=user.school).select_related("policy")
        # Optionally filter by policy id
        policy_id = self.request.query_params.get("policy", None)
        if policy_id:
            qs = qs.filter(policy_id=policy_id)
        return qs

    def perform_create(self, serializer):
        """
        Ensure the policy belongs to the user's school before creating the AssessmentType.
        """
        policy = serializer.validated_data.get("policy")
        if policy.school != self.request.user.school:
            raise ValueError("Policy does not belong to your school.")
        serializer.save()

    def perform_update(self, serializer):
        # Prevent moving an assessment type to another school's policy
        policy = serializer.validated_data.get("policy", getattr(serializer.instance, "policy", None))
        if policy and policy.school != self.request.user.school:
            raise ValueError("Policy does not belong to your school.")
        serializer.save()
