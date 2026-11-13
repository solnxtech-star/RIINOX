import uuid

from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.applications.inventory.api.permissions import (
    InventoryLedgerPermission,
    InventoryPermission,
    StockAdjustmentPermission,
    StockTransferPermission,
)
from core.applications.inventory.api.schemas import (
    inventory_schema,
    ledger_entry_schema,
    stock_adjustment_schema,
    stock_transfer_schema,
)
from core.applications.inventory.api.serializers import (
    InventoryLedgerEntrySerializer,
    InventorySerializer,
    InventorySummarySerializer,
    StockAdjustmentRequestSerializer,
    StockTransferSerializer,
)
from core.applications.inventory.models import (
    Inventory,
    InventoryLedgerEntry,
    StockAdjustmentRequest,
)
from core.applications.inventory.services import (
    InsufficientStock,
    StockAdjustmentInput,
    StockTransferInput,
    approve_stock_adjustment,
    get_inventory_summary,
    request_stock_adjustment,
    single_transfer_stock,
)
from core.applications.products.models import Product, ProductBatch, ProductVariant
from core.applications.users.permissions import OrganizationScopedMixin
from core.applications.warehouse.models import Warehouse


@stock_adjustment_schema
class StockAdjustmentViewSet(
    OrganizationScopedMixin,
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = StockAdjustmentRequestSerializer
    permission_classes = [IsAuthenticated, StockAdjustmentPermission]

    def get_queryset(self):
        user = self.request.user
        if not user.is_authenticated:
            return StockAdjustmentRequest.objects.none()

        if not self.organization:
            return StockAdjustmentRequest.objects.none()

        queryset = StockAdjustmentRequest.objects.for_organization(self.organization).with_details()
        return self.scope_to_locations(queryset, field="warehouse")

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data, context={"request": request, "view": self})
        serializer.is_valid(raise_exception=True)

        warehouse = serializer.validated_data["warehouse"]
        product = serializer.validated_data["product"]

        if self.organization:
            if warehouse.organization_id != self.organization.id:
                raise ValidationError({"warehouse": "Warehouse does not belong to this organization."})
            if product.organization_id != self.organization.id:
                raise ValidationError({"product": "Product does not belong to this organization."})
        self.location_scope.require(warehouse)

        adj = request_stock_adjustment(
            StockAdjustmentInput(
                product=product,
                variant=serializer.validated_data.get("variant"),
                batch=serializer.validated_data.get("batch"),
                warehouse=warehouse,
                quantity_change=serializer.validated_data["requested_quantity_change"],
                adjustment_reason=serializer.validated_data.get("adjustment_reason"),
                notes=serializer.validated_data.get("notes"),
                requested_unit=serializer.validated_data.get("requested_unit"),
            ),
            user=request.user,
        )
        return Response(
            StockAdjustmentRequestSerializer(adj, context={"request": request, "view": self}).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["post"])
    def approve(self, request, organization_id=None, pk=None):
        # get_object() scopes to this organization and the caller's locations, so an
        # id from another tenant or location is a 404 before anything is touched.
        adjustment = self.get_object()
        try:
            approve_stock_adjustment(adjustment.pk, request.user, organization=self.organization)
            return Response({"status": "approved"}, status=status.HTTP_200_OK)
        except ValueError as e:
            return Response({"code": "APPROVAL_ERROR", "detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)


@stock_transfer_schema
class StockTransferViewSet(OrganizationScopedMixin, viewsets.ViewSet):
    permission_classes = [IsAuthenticated, StockTransferPermission]

    @action(detail=False, methods=["post"])
    def execute(self, request, organization_id=None):
        serializer = StockTransferSerializer(data=request.data, context={"request": request, "view": self})
        serializer.is_valid(raise_exception=True)

        data = serializer.validated_data
        org = self.organization
        try:
            # Every lookup is tenant-scoped: an id from another organization is "not found".
            product = Product.objects.get(id=data["product_id"], organization=org)
            variant = (
                ProductVariant.objects.get(id=data["variant_id"], product=product)
                if data.get("variant_id")
                else None
            )
            batch = ProductBatch.objects.get(id=data["batch_id"], product=product) if data.get("batch_id") else None
            source_wh = Warehouse.objects.get(id=data["source_warehouse_id"], organization=org)
            dest_wh = Warehouse.objects.get(id=data["destination_warehouse_id"], organization=org)
        except (Product.DoesNotExist, ProductVariant.DoesNotExist, ProductBatch.DoesNotExist, Warehouse.DoesNotExist):
            raise ValidationError(
                {"detail": "Product, variant, batch or warehouse not found in this organization."}
            ) from None

        # A transfer touches both locations, so the caller needs access to both.
        self.location_scope.require(source_wh)
        self.location_scope.require(dest_wh)

        try:
            single_transfer_stock(
                StockTransferInput(
                    source_warehouse=source_wh,
                    destination_warehouse=dest_wh,
                    product=product,
                    variant=variant,
                    batch=batch,
                    quantity=data["quantity"],
                ),
                user=request.user,
            )
            return Response({"status": "transfer completed"}, status=status.HTTP_200_OK)
        except (InsufficientStock, ValueError) as e:
            # Business failures only. Plan limits, permissions and validation errors
            # propagate with their own codes; unexpected errors are 500s, not hidden.
            return Response({"code": "TRANSFER_ERROR", "detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)


@inventory_schema
class InventoryViewSet(OrganizationScopedMixin, viewsets.ReadOnlyModelViewSet):
    serializer_class = InventorySerializer
    permission_classes = [IsAuthenticated, InventoryPermission]

    def get_serializer_class(self):
        if self.action == "summary":
            return InventorySummarySerializer
        return InventorySerializer

    def get_queryset(self):
        user = self.request.user
        if not user.is_authenticated:
            return Inventory.objects.none()

        if not self.organization:
            return Inventory.objects.none()

        queryset = Inventory.objects.for_organization(self.organization).with_details()
        queryset = _filter_by_product(queryset, self.request.query_params)
        return self.scope_to_locations(queryset, field="warehouse")


@ledger_entry_schema
class InventoryLedgerEntryViewSet(OrganizationScopedMixin, viewsets.ReadOnlyModelViewSet):
    serializer_class = InventoryLedgerEntrySerializer
    permission_classes = [IsAuthenticated, InventoryLedgerPermission]

    def get_queryset(self):
        user = self.request.user
        if not user.is_authenticated:
            return InventoryLedgerEntry.objects.none()

        if not self.organization:
            return InventoryLedgerEntry.objects.none()

        queryset = InventoryLedgerEntry.objects.for_organization(self.organization).with_details()
        queryset = _filter_by_product(queryset, self.request.query_params)
        return self.scope_to_locations(queryset, field="warehouse")


def _filter_by_product(queryset, query_params):
    """`?product=<uuid>`. A malformed id is a 400, never a 500."""
    raw = query_params.get("product")
    if not raw:
        return queryset
    try:
        product_id = uuid.UUID(raw.strip())
    except ValueError:
        raise ValidationError({"product": ["Must be a valid product id."]}) from None
    return queryset.filter(product_id=product_id)
