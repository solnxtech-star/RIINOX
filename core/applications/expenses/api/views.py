from __future__ import annotations

from datetime import date

from rest_framework import mixins
from rest_framework import status
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.filters import OrderingFilter
from rest_framework.filters import SearchFilter
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.applications.expenses.api.permissions import ExpensePermission
from core.applications.expenses.api.schemas import expense_schema
from core.applications.expenses.api.serializers import ExpenseCreateSerializer
from core.applications.expenses.api.serializers import ExpenseSerializer
from core.applications.expenses.api.serializers import ExpenseUpdateSerializer
from core.applications.expenses.api.serializers import ExpenseVoidSerializer
from core.applications.expenses.models import Expense
from core.applications.expenses.services import ExpenseInput
from core.applications.expenses.services import record_expense
from core.applications.expenses.services import update_expense
from core.applications.expenses.services import void_expense
from core.applications.notification.audit.context import AuditContext
from core.applications.users.permissions import OrganizationScopedMixin
from core.helper.enums import ExpenseCategory
from core.helper.enums import ExpenseStatus


@expense_schema
class ExpenseViewSet(
    OrganizationScopedMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """Operating expenses. No PUT and no DELETE: corrections are voids (PRD §44)."""

    permission_classes = [IsAuthenticated, ExpensePermission]
    serializer_class = ExpenseSerializer
    http_method_names = ["get", "post", "patch", "head", "options"]
    filter_backends = [SearchFilter, OrderingFilter]
    search_fields = ["expense_number", "title", "paid_to", "reference"]
    ordering_fields = ["expense_date", "amount", "created_at"]
    ordering = ["-expense_date", "-created_at"]

    def get_queryset(self):
        if not self.request.user.is_authenticated or not self.organization:
            return Expense.objects.none()
        queryset = Expense.objects.for_organization(self.organization).with_details()
        queryset = _apply_filters(queryset, self.request.query_params)
        return self.scope_to_locations(queryset, field="location")

    def get_serializer_context(self):
        return {**super().get_serializer_context(), "organization": self.organization}

    def create(self, request, *args, **kwargs):
        serializer = ExpenseCreateSerializer(data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        self.location_scope.require(data["location"])
        expense = record_expense(
            organization=self.organization,
            actor=request.user,
            data=ExpenseInput(**data),
            audit_context=AuditContext.from_request(request),
        )
        return Response(self._read(expense), status=status.HTTP_201_CREATED)

    def partial_update(self, request, *args, **kwargs):
        expense = self.get_object()
        serializer = ExpenseUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        expense = update_expense(
            expense,
            actor=request.user,
            changes=dict(serializer.validated_data),
            audit_context=AuditContext.from_request(request),
        )
        return Response(self._read(expense))

    @action(detail=True, methods=["post"])
    def void(self, request, *args, **kwargs):
        expense = self.get_object()
        serializer = ExpenseVoidSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        expense = void_expense(
            expense,
            actor=request.user,
            reason=serializer.validated_data["reason"],
            audit_context=AuditContext.from_request(request),
        )
        return Response(self._read(expense))

    def _read(self, expense):
        expense = Expense.objects.with_details().get(pk=expense.pk)
        return ExpenseSerializer(expense, context=self.get_serializer_context()).data


def _apply_filters(queryset, params):
    category = params.get("category")
    if category:
        if category not in ExpenseCategory.values:
            raise ValidationError({"category": [f"Must be one of: {', '.join(ExpenseCategory.values)}."]})
        queryset = queryset.filter(category=category)

    status_value = params.get("status")
    if status_value:
        if status_value not in ExpenseStatus.values:
            raise ValidationError({"status": [f"Must be one of: {', '.join(ExpenseStatus.values)}."]})
        queryset = queryset.filter(status=status_value)

    date_from, date_to = _date(params, "date_from"), _date(params, "date_to")
    if date_from and date_to and date_from > date_to:
        raise ValidationError({"date_to": ["Must be on or after date_from."]})
    if date_from:
        queryset = queryset.filter(expense_date__gte=date_from)
    if date_to:
        queryset = queryset.filter(expense_date__lte=date_to)
    return queryset


def _date(params, name: str) -> date | None:
    raw = params.get(name)
    if not raw:
        return None
    try:
        return date.fromisoformat(raw.strip())
    except ValueError:
        raise ValidationError({name: ["Use YYYY-MM-DD."]}) from None
