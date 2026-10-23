from __future__ import annotations

from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.applications.sales.api.permissions import (
    SalePermission,
    SaleReturnPermission,
)
from core.applications.sales.api.schemas import (
    sale_return_viewset_schema,
    sale_viewset_schema,
)
from core.applications.sales.api.serializers import (
    SaleCancelSerializer,
    SaleCreateSerializer,
    SaleReturnCreateSerializer,
    SaleReturnSerializer,
    SaleSerializer,
)
from core.applications.sales.filters import SaleFilterSet, SaleReturnFilterSet
from core.applications.sales.models import Sale, SaleReturn
from core.applications.sales.services import (
    SaleCancellationError,
    cancel_sale,
    create_sale,
    create_sale_return,
)
from core.applications.users.permissions import OrganizationScopedMixin


@sale_viewset_schema
class SaleViewSet(OrganizationScopedMixin, viewsets.ModelViewSet):
    """
    Tenant-scoped ViewSet for Sales Orders (PRD §21, §44).
    Enforces active subscription, customer row locks, FIFO stock deduction,
    immutability against hard deletion, and cancellation with inventory reversals.
    """
    queryset = Sale.objects.none()
    permission_classes = [IsAuthenticated, SalePermission]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_class = SaleFilterSet
    search_fields = ["sale_id", "reference_number", "customer__name"]
    ordering_fields = ["created_at", "total", "due_date"]
    ordering = ["-created_at"]

    def get_queryset(self):
        user = self.request.user
        if not user.is_authenticated or not self.organization:
            return Sale.objects.none()

        return Sale.objects.for_organization(self.organization).with_details()

    def get_serializer_class(self):
        if self.action in ["create"]:
            return SaleCreateSerializer
        if self.action in ["cancel"]:
            return SaleCancelSerializer
        return SaleSerializer

    def create(self, request, *args, **kwargs):
        serializer = SaleCreateSerializer(data=request.data, context={"request": request, "view": self})
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        if "organization" not in data and self.organization:
            data["organization"] = self.organization
        sale = create_sale(data, actor=request.user)
        response_serializer = SaleSerializer(sale, context={"request": request, "view": self})
        return Response(response_serializer.data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        return Response(
            {
                "detail": "Historical sales orders cannot be modified directly (PRD §44). "
                "Cancel the order or initiate a sales return to make corrections."
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    def partial_update(self, request, *args, **kwargs):
        return self.update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        return Response(
            {
                "detail": "Historical sales orders cannot be hard-deleted (PRD §44). "
                "Use the /cancel/ endpoint to cancel a sale and reverse inventory movements."
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None, **kwargs):
        """
        Cancel a sale, reverse its inventory movements, and record compensatory
        ledger entries (PRD §44).
        """
        sale = self.get_object()
        serializer = SaleCancelSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reason = serializer.validated_data.get("reason", "")
        try:
            cancelled_sale = cancel_sale(sale, actor=request.user, reason=reason)
        except SaleCancellationError as exc:
            return Response(
                {"code": exc.code, "detail": exc.message},
                status=status.HTTP_400_BAD_REQUEST,
            )
        response_serializer = SaleSerializer(cancelled_sale, context={"request": request, "view": self})
        return Response(response_serializer.data, status=status.HTTP_200_OK)


@sale_return_viewset_schema
class SaleReturnViewSet(OrganizationScopedMixin, viewsets.ModelViewSet):
    """
    Tenant-scoped ViewSet for Sales Returns and Refunds (PRD §23).
    Atomically updates returned line quantities, triggers restock movements,
    and refunds payments.
    """
    queryset = SaleReturn.objects.none()
    permission_classes = [IsAuthenticated, SaleReturnPermission]
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_class = SaleReturnFilterSet
    ordering = ["-created_at"]

    def get_queryset(self):
        user = self.request.user
        if not user.is_authenticated or not self.organization:
            return SaleReturn.objects.none()

        return SaleReturn.objects.for_organization(self.organization).with_details()

    def get_serializer_class(self):
        if self.action in ["create"]:
            return SaleReturnCreateSerializer
        return SaleReturnSerializer

    def create(self, request, *args, **kwargs):
        serializer = SaleReturnCreateSerializer(data=request.data, context={"request": request, "view": self})
        serializer.is_valid(raise_exception=True)
        sale_return = create_sale_return(serializer.validated_data, actor=request.user)
        response_serializer = SaleReturnSerializer(sale_return, context={"request": request, "view": self})
        return Response(response_serializer.data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        return Response(
            {"detail": "Sale returns are immutable records and cannot be modified."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    def partial_update(self, request, *args, **kwargs):
        return self.update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        return Response(
            {"detail": "Sale returns are immutable records and cannot be deleted."},
            status=status.HTTP_400_BAD_REQUEST,
        )
