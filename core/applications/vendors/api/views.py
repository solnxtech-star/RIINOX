from __future__ import annotations

from drf_spectacular.utils import extend_schema
from rest_framework import filters, viewsets
from rest_framework.permissions import IsAuthenticated

from core.applications.purchase.api.permissions import PurchasePermission
from core.applications.users.permissions import OrganizationScopedMixin
from core.applications.vendors.api.serializers import VendorSerializer
from core.applications.vendors.models import Vendor


@extend_schema(tags=["Vendors"])
class VendorViewSet(OrganizationScopedMixin, viewsets.ModelViewSet):
    """
    CRUD ViewSet for Suppliers / Vendors scoped to the tenant organization.
    """
    queryset = Vendor.objects.none()
    permission_classes = [IsAuthenticated, PurchasePermission]
    serializer_class = VendorSerializer
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["company_name", "contact_person", "phone", "email"]
    ordering_fields = ["company_name", "created_at"]
    ordering = ["company_name"]

    def get_queryset(self):
        user = self.request.user
        org = self.organization
        if not org:
            return Vendor.objects.none()
        return Vendor.objects.for_organization(org).for_user(user)

    def perform_create(self, serializer):
        serializer.save(organization=self.organization)
