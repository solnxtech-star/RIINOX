from __future__ import annotations

from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.applications.customers.api.permissions import CustomerPermission
from core.applications.customers.api.schemas import customer_schema
from core.applications.customers.api.serializers import (
    CustomerCreateSerializer,
    CustomerCreditAdjustmentSerializer,
    CustomerSerializer,
)
from core.applications.customers.models import Customer
from core.applications.customers.services import adjust_customer_credit, delete_customer
from core.applications.users.permissions import OrganizationScopedMixin


@customer_schema
class CustomerViewSet(OrganizationScopedMixin, viewsets.ModelViewSet):
    """
    Tenant-scoped ViewSet for Customer records (PRD §20).
    Enforces URL-based organization boundaries, granular role permissions,
    and automatic fallback from deletion to soft-deactivation when historical records exist.
    """

    permission_classes = [IsAuthenticated, CustomerPermission]
    filter_backends = [SearchFilter, OrderingFilter]
    search_fields = ["name", "company_name", "email", "phone"]
    ordering_fields = ["name", "credit_limit", "balance", "created_at", "updated_at"]
    ordering = ["name"]

    def get_queryset(self):
        """
        Returns customers strictly scoped to the active organization extracted from URL path.
        """
        user = self.request.user
        if not user.is_authenticated or not self.organization:
            return Customer.objects.none()

        qs = Customer.objects.for_organization(self.organization).with_details()

        # Query param filtering
        status_param = self.request.query_params.get("status")
        if status_param:
            qs = qs.filter(status=status_param.lower())

        type_param = self.request.query_params.get("customer_type")
        if type_param:
            qs = qs.filter(customer_type=type_param.lower())

        return qs

    def get_serializer_class(self):
        if self.action in ["create", "update", "partial_update"]:
            return CustomerCreateSerializer
        if self.action == "adjust_credit":
            return CustomerCreditAdjustmentSerializer
        return CustomerSerializer

    def destroy(self, request, *args, **kwargs):
        """
        Deletes a customer if unreferenced. If linked to existing transactions
        (sales orders, invoices), database constraints trigger a graceful fallback
        to soft-deactivation (PRD §44).
        """
        instance = self.get_object()
        deleted, customer = delete_customer(customer=instance, user=request.user)

        if deleted:
            return Response(status=status.HTTP_204_NO_CONTENT)

        return Response(
            {"detail": f"Customer '{customer.name}' has existing records and was deactivated instead of deleted."},
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["post"])
    def adjust_credit(self, request, pk=None, organization_id=None):
        """
        Explicit credit limit and terms governance action (PRD §26).
        Requires MANAGE_CUSTOMER_CREDIT permission.
        """
        customer = self.get_object()
        serializer = CustomerCreditAdjustmentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        updated_customer = adjust_customer_credit(
            customer=customer,
            user=request.user,
            credit_limit=serializer.validated_data["credit_limit"],
            payment_terms_days=serializer.validated_data.get("payment_terms_days"),
            reason=serializer.validated_data.get("reason", ""),
        )
        return Response(CustomerSerializer(updated_customer).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["get"])
    def sales(self, request, pk=None, organization_id=None):
        """
        Returns recent sales history placed by this customer (PRD §20).
        """
        customer = self.get_object()
        if hasattr(customer, "sales"):
            sales_qs = customer.sales.all().order_by("-created_at")
            page = self.paginate_queryset(sales_qs)
            if page is not None:
                from core.applications.sales.api.serializers import SaleSerializer
                serializer = SaleSerializer(page, many=True, context=self.get_serializer_context())
                return self.get_paginated_response(serializer.data)
        return Response([])
