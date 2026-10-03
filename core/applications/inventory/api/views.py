from rest_framework import viewsets, mixins, status
from rest_framework.decorators import action
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema, extend_schema_view, OpenApiResponse
from core.applications.inventory.models import StockAdjustmentRequest, Inventory, InventoryLedgerEntry
from core.applications.products.models import Product, ProductVariant, ProductBatch
from core.applications.warehouse.models import Warehouse
from core.applications.inventory.api.serializers import (
    StockAdjustmentRequestSerializer, StockTransferSerializer, 
    InventorySerializer, InventoryLedgerEntrySerializer
)
from core.applications.inventory.services import request_stock_adjustment, approve_stock_adjustment, transfer_stock
from core.applications.inventory.api.schemas import (
    stock_adjustment_create_example, stock_transfer_example,
    inventory_schema, ledger_entry_schema
)

@extend_schema_view(
    create=extend_schema(
        tags=['Inventory'],
        examples=[stock_adjustment_create_example]
    ),
    list=extend_schema(tags=['Inventory']),
    retrieve=extend_schema(tags=['Inventory'])
)
class StockAdjustmentViewSet(mixins.CreateModelMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    queryset = StockAdjustmentRequest.objects.all()
    serializer_class = StockAdjustmentRequestSerializer

    def create(self, request, *args, **kwargs):
        # Instead of directly creating, use the service layer
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        adj = request_stock_adjustment(
            product=serializer.validated_data['product'],
            variant=serializer.validated_data.get('variant'),
            batch=serializer.validated_data.get('batch'),
            warehouse=serializer.validated_data['warehouse'],
            quantity_change=serializer.validated_data['requested_quantity_change'],
            reason=serializer.validated_data['reason'],
            user=request.user
        )
        return Response(StockAdjustmentRequestSerializer(adj).data, status=status.HTTP_201_CREATED)

    @extend_schema(
        tags=['Inventory'],
        responses={200: OpenApiResponse(description="Adjustment Approved")}
    )
    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        try:
            approve_stock_adjustment(pk, request.user)
            return Response({"status": "approved"}, status=status.HTTP_200_OK)
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

class StockTransferViewSet(viewsets.ViewSet):
    @extend_schema(
        tags=['Inventory'],
        request=StockTransferSerializer,
        examples=[stock_transfer_example],
        responses={200: OpenApiResponse(description="Transfer Successful")}
    )
    @action(detail=False, methods=['post'])
    def execute(self, request):
        serializer = StockTransferSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        data = serializer.validated_data
        try:
            product = Product.objects.get(id=data['product_id'])
            variant = ProductVariant.objects.get(id=data['variant_id']) if data.get('variant_id') else None
            batch = ProductBatch.objects.get(id=data['batch_id']) if data.get('batch_id') else None
            source_wh = Warehouse.objects.get(id=data['source_warehouse_id'])
            dest_wh = Warehouse.objects.get(id=data['destination_warehouse_id'])

            transfer_stock(
                source_wh=source_wh,
                dest_wh=dest_wh,
                product=product,
                variant=variant,
                batch=batch,
                quantity=data['quantity'],
                user=request.user
            )
            return Response({"status": "transfer completed"}, status=status.HTTP_200_OK)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

@inventory_schema
class InventoryViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Inventory.objects.select_related('product', 'variant', 'batch', 'warehouse').all()
    serializer_class = InventorySerializer

@ledger_entry_schema
class InventoryLedgerEntryViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = InventoryLedgerEntry.objects.select_related('transaction', 'product', 'variant', 'batch', 'warehouse').all()
    serializer_class = InventoryLedgerEntrySerializer
