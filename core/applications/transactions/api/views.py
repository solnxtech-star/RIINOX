from __future__ import annotations

from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated

from core.applications.users.permissions import OrganizationScopedMixin
from core.applications.transactions.models import Transaction, Customer
from core.applications.transactions.api.permissions import (
    CustomerPermission,
    TransactionPermission,
)
from core.applications.transactions.api.serializers import (
    CustomerSerializer,
    TransactionSerializer,
)
from core.applications.transactions.api.schemas import (
    customer_schema,
    transaction_schema,
)


@customer_schema
class CustomerViewSet(OrganizationScopedMixin, viewsets.ModelViewSet):
    """
    CRUD ViewSet for Customers scoped to the tenant organization.
    """
    queryset = Customer.objects.none()
    permission_classes = [IsAuthenticated, CustomerPermission]
    serializer_class = CustomerSerializer

    def get_queryset(self):
        user = self.request.user
        org = self.organization
        if not org:
            return Customer.objects.none()
        return Customer.objects.for_organization(org).for_user(user)

    def perform_create(self, serializer):
        serializer.save(organization=self.organization)


@transaction_schema
class TransactionViewSet(OrganizationScopedMixin, viewsets.ModelViewSet):
    """
    Unified business event Transaction ViewSet scoped to the tenant organization.
    """
    queryset = Transaction.objects.none()
    permission_classes = [IsAuthenticated, TransactionPermission]
    serializer_class = TransactionSerializer

    def get_queryset(self):
        user = self.request.user
        org = self.organization
        if not org:
            return Transaction.objects.none()
        return Transaction.objects.for_organization(org).for_user(user).with_entries()

    def perform_create(self, serializer):
        serializer.save(organization=self.organization)
