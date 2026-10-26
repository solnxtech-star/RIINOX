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
    StockAdjustmentRequestSerializer,
    StockTransferSerializer,
)
from core.applications.inventory.models import (
    Inventory,
    InventoryLedgerEntry,
    StockAdjustmentRequest,
)
from core.applications.inventory.services import (
    StockAdjustmentInput,
    StockTransferInput,
    approve_stock_adjustment,
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

        return StockAdjustmentRequest.objects.for_organization(self.organization).with_details()

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
        try:
            approve_stock_adjustment(pk, request.user)
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
        try:
            product = Product.objects.get(id=data["product_id"])
            variant = ProductVariant.objects.get(id=data["variant_id"]) if data.get("variant_id") else None
            batch = ProductBatch.objects.get(id=data["batch_id"]) if data.get("batch_id") else None
            source_wh = Warehouse.objects.get(id=data["source_warehouse_id"])
            dest_wh = Warehouse.objects.get(id=data["destination_warehouse_id"])

            if self.organization:
                if source_wh.organization_id != self.organization.id or dest_wh.organization_id != self.organization.id:
                    raise ValidationError({"warehouse": "Warehouses must belong to this organization."})
                if product.organization_id != self.organization.id:
                    raise ValidationError({"product": "Product must belong to this organization."})

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
        except ValidationError as e:
            raise e
        except Exception as e:
            return Response({"code": "TRANSFER_ERROR", "detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)


@inventory_schema
class InventoryViewSet(OrganizationScopedMixin, viewsets.ReadOnlyModelViewSet):
    serializer_class = InventorySerializer
    permission_classes = [IsAuthenticated, InventoryPermission]

    def get_queryset(self):
        user = self.request.user
        if not user.is_authenticated:
            return Inventory.objects.none()

        if not self.organization:
            return Inventory.objects.none()

        return Inventory.objects.for_organization(self.organization).with_details()


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

        return InventoryLedgerEntry.objects.for_organization(self.organization).with_details()
