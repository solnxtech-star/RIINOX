from rest_framework import viewsets, mixins, status
from rest_framework.decorators import action
from rest_framework.filters import SearchFilter, OrderingFilter
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.db.models import ProtectedError
from django.shortcuts import get_object_or_404

from core.applications.products.models import Product, ProductVariant
from core.applications.products.api.serializers import (
    ProductSerializer,
    ProductCreateSerializer,
    ProductVariantSerializer,
)
from core.applications.products.api.schemas import product_schema
from core.applications.products.api.permissions import ProductPermission
from core.applications.users.permissions import OrganizationScopedMixin
from core.applications.products.services import archive_product, restore_product

from core.applications.notification.audit.services import record as audit_record
from core.applications.notification.audit.context import AuditContext
from core.applications.notification.audit.action import AuditAction

@product_schema
class ProductViewSet(OrganizationScopedMixin, viewsets.ModelViewSet):
    """
    Tenant-scoped ViewSet for Products.
    Enforces URL-based organization boundaries, granular role permissions,
    and automatic fallback from deletion to soft-archiving when records exist.
    """
    permission_classes = [IsAuthenticated, ProductPermission]
    filter_backends = [SearchFilter, OrderingFilter]
    search_fields = ["name", "sku", "barcode"]
    ordering_fields = ["name", "customer_sale_price", "created_at", "updated_at"]
    ordering = ["name"]

    def get_queryset(self):
        """
        Returns products strictly scoped to the active organization extracted from URL path.
        Includes optimized prefetching of batches, discounts, conversions, and variants.
        """
        user = self.request.user
        if not user.is_authenticated:
            return Product.objects.none()

        if not self.organization:
            return Product.objects.none()

        # Eager load related entities to eliminate N+1 queries across nested representations
        qs = Product.objects.for_organization(self.organization).with_details()

        # Query param filters (PRD §13/§14/§47)
        status_param = self.request.query_params.get("status")
        if status_param:
            qs = qs.filter(status=status_param)
        category_param = self.request.query_params.get("category")
        if category_param:
            qs = qs.filter(category_id=category_param)
        brand_param = self.request.query_params.get("brand")
        if brand_param:
            qs = qs.filter(brand_id=brand_param)
        product_type = self.request.query_params.get("product_type")
        if product_type:
            qs = qs.filter(product_type=product_type)

        return qs
    
    def get_serializer_class(self):
        if self.action in ['create', 'update', 'partial_update']:
            return ProductCreateSerializer

        return ProductSerializer

    def perform_create(self, serializer):
        product = serializer.save(created_by=self.request.user, organization=self.organization)
        audit_record(
            action=AuditAction.PRODUCT_CREATED,
            organization=product.organization,
            actor=self.request.user,
            resource=product,
            context=AuditContext.from_request(self.request),
            metadata={"sku": product.sku}
        )

    def perform_update(self, serializer):
        old_instance = self.get_object()
        old_data = {
            "name": old_instance.name,
            "category_id": str(old_instance.category_id) if old_instance.category_id else None,
            "status": old_instance.status,
            "customer_sale_price": str(old_instance.customer_sale_price) if old_instance.customer_sale_price is not None else None
        }
        product = serializer.save()
        new_data = {
            "name": product.name,
            "category_id": str(product.category_id) if product.category_id else None,
            "status": product.status,
            "customer_sale_price": str(product.customer_sale_price) if product.customer_sale_price is not None else None
        }
        audit_record(
            action=AuditAction.PRODUCT_UPDATED,
            organization=product.organization,
            actor=self.request.user,
            resource=product,
            previous_values=old_data,
            new_values=new_data,
            context=AuditContext.from_request(self.request)
        )

    def destroy(self, request, *args, **kwargs):
        """
        Deletes a product if unreferenced. If the product is linked to existing transactions
        (invoices, stock movements, etc.), database PROTECT constraints trigger ProtectedError.
        Strategic Behavior (PRD §44):
        Instead of returning a raw 500 or blocking error, we catch ProtectedError and gracefully
        soft-archive the product, preserving historical ledgers while retiring it from active sale.
        """
        instance = self.get_object()
        try:
            self.perform_destroy(instance)
            return Response(status=status.HTTP_204_NO_CONTENT)
        except ProtectedError:
            archive_product(
                product=instance,
                user=request.user,
                reason="Archived due to existing records preventing deletion."
            )
            return Response(
                {"detail": f"'{instance.name}' has existing records and was archived instead of deleted."},
                status=status.HTTP_200_OK
            )

    @action(detail=True, methods=["post"])
    def archive(self, request, pk=None, organization_id=None):
        """
        Explicitly soft-archive an active product (PRD §44).
        Excludes the product from active catalog searches and plan limit calculations.
        """
        instance = self.get_object()
        reason = request.data.get("reason", "Archived via API.")
        archive_product(product=instance, user=request.user, reason=reason)
        return Response({"detail": f"'{instance.name}' has been archived."}, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"])
    def restore(self, request, pk=None, organization_id=None):
        """
        Restore an archived product back to active status (PRD §44).
        Enforces plan quota checks before permitting reactivation.
        """
        instance = self.get_object()
        restore_product(product=instance, user=request.user)
        return Response({"detail": f"'{instance.name}' has been restored to active status."}, status=status.HTTP_200_OK)


class ProductVariantViewSet(OrganizationScopedMixin, viewsets.ModelViewSet):
    """
    Organization-scoped management of individual Product Variants (PRD §13/§14).
    Enforces tenant boundaries and permission checks for dedicated sub-resource operations:
    GET/POST /api/organizations/{org_id}/products/{product_id}/variants/
    """
    permission_classes = [IsAuthenticated, ProductPermission]
    serializer_class = ProductVariantSerializer
    filter_backends = [SearchFilter, OrderingFilter]
    search_fields = ["name", "sku", "barcode"]
    ordering_fields = ["name", "customer_sale_price", "created_at"]
    ordering = ["name"]

    def get_queryset(self):
        """
        Returns variants scoped strictly to the current tenant organization and the parent product.
        """
        user = self.request.user
        if not user.is_authenticated or not self.organization:
            return ProductVariant.objects.none()

        qs = ProductVariant.objects.for_organization(self.organization)
        product_id = self.kwargs.get("product_id")
        if product_id:
            qs = qs.filter(product_id=product_id)
        return qs

    def perform_create(self, serializer):
        """
        Ensures the parent product exists within the active organization before attaching the variant.
        """
        product_id = self.kwargs.get("product_id")
        product = get_object_or_404(Product, id=product_id, organization=self.organization)
        serializer.save(product=product)

