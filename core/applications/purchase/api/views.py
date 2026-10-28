import csv

from django.http import HttpResponse
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.applications.inventory.services import get_purchase_stock_activity
from core.applications.invoice.models import PurchaseInvoice
from core.applications.notification.audit.context import AuditContext
from core.applications.payment.models import PurchasePayment
from core.applications.products.models import ProductBatch
from core.applications.purchase.api.permissions import PurchasePermission
from core.applications.purchase.api.schemas import purchase_schema
from core.applications.purchase.api.serializers import (
    PurchaseCancelSerializer,
    PurchaseCreateSerializer,
    PurchaseInvoiceCreateSerializer,
    PurchaseInvoiceSerializer,
    PurchasePaymentCreateSerializer,
    PurchasePaymentSerializer,
    PurchaseReceiveSerializer,
    PurchaseReturnCreateSerializer,
    PurchaseReturnSerializer,
    PurchaseSerializer,
    PurchaseStockActivitySerializer,
    PurchaseSummarySerializer,
    PurchaseUpdateSerializer,
)
from core.applications.purchase.filters import PurchaseFilterSet
from core.applications.purchase.models import Purchase, PurchaseReturn
from core.applications.purchase.services import (
    PurchaseInvoiceInput,
    PurchasePaymentInput,
    cancel_purchase_order,
    create_purchase_order,
    create_purchase_return,
    delete_purchase_order,
    get_purchase_summary,
    receive_purchase_order,
    record_purchase_invoice,
    record_supplier_payment,
    update_purchase_order,
)
from core.applications.users.permissions import OrganizationScopedMixin
from core.helper.custom_exceptions import BusinessRuleError


@purchase_schema
class PurchaseViewSet(OrganizationScopedMixin, viewsets.ModelViewSet):
    queryset = Purchase.objects.none()
    permission_classes = [IsAuthenticated, PurchasePermission]
    filter_backends = [
        DjangoFilterBackend,
        filters.SearchFilter,
        filters.OrderingFilter,
    ]
    filterset_class = PurchaseFilterSet
    search_fields = [
        "purchase_number",
        "vendor__company_name",
        "vendor_invoice_number",
        "notes",
    ]
    ordering_fields = [
        "created_at",
        "order_date",
        "delivery_date",
        "purchase_number",
        "status",
        "payment_status",
        "amount_paid",
    ]
    ordering = ["-created_at"]

    def get_queryset(self):
        user = self.request.user
        org = self.organization
        if not org:
            return Purchase.objects.none()
        return Purchase.objects.for_organization(org).for_user(user).with_details()

    def get_serializer_class(self):
        is_post = self.request.method == "POST"
        serializer_map = {
            "create": PurchaseCreateSerializer,
            "update": PurchaseUpdateSerializer,
            "partial_update": PurchaseUpdateSerializer,
            "summary": PurchaseSummarySerializer,
            "cancel": PurchaseCancelSerializer,
            "receive": PurchaseReceiveSerializer,
            "purchase_invoice": PurchaseInvoiceCreateSerializer if is_post else PurchaseInvoiceSerializer,
            "payments": PurchasePaymentCreateSerializer if is_post else PurchasePaymentSerializer,
            "returns": PurchaseReturnCreateSerializer if is_post else PurchaseReturnSerializer,
            "stock_activity": PurchaseStockActivitySerializer,
        }
        return serializer_map.get(self.action, PurchaseSerializer)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        validated_data = dict(serializer.validated_data)
        items_data = validated_data.pop("items", [])
        validated_data.pop("organization", None)
        ordered_by = validated_data.pop("ordered_by", None) or request.user
        vendor = validated_data.pop("vendor")
        warehouse = validated_data.pop("warehouse")

        purchase = create_purchase_order(
            organization=self.organization,
            ordered_by=ordered_by,
            vendor=vendor,
            warehouse=warehouse,
            items_data=items_data,
            audit_context=AuditContext.from_request(request),
            **validated_data,
        )
        output = PurchaseSerializer(purchase, context=self.get_serializer_context())
        return Response(output.data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        validated_data = dict(serializer.validated_data)
        items_data = validated_data.pop("items", None)
        validated_data.pop("organization", None)

        purchase = update_purchase_order(
            purchase=instance,
            actor=request.user,
            items_data=items_data,
            audit_context=AuditContext.from_request(request),
            **validated_data,
        )
        output = PurchaseSerializer(purchase, context=self.get_serializer_context())
        return Response(output.data, status=status.HTTP_200_OK)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        delete_purchase_order(
            purchase=instance,
            actor=request.user,
            audit_context=AuditContext.from_request(request),
        )
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=False, methods=["get"], url_path="summary")
    def summary(self, request, *args, **kwargs):
        data = get_purchase_summary(self.organization)
        serializer = PurchaseSummarySerializer(data)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=False, methods=["get"], url_path="export")
    def export(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="purchase_orders.csv"'
        writer = csv.writer(response)
        writer.writerow(
            [
                "Purchase Number",
                "Supplier",
                "Warehouse",
                "Order Date",
                "Delivery Date",
                "Status",
                "Payment Status",
                "Ordered Value",
                "Total Received Value",
                "Amount Paid",
                "Balance Due",
                "Vendor Invoice Number",
                "Created At",
            ]
        )
        for po in queryset:
            writer.writerow(
                [
                    po.purchase_number,
                    po.vendor.company_name if po.vendor else "",
                    po.warehouse.name if po.warehouse else "",
                    po.order_date or "",
                    po.delivery_date or "",
                    po.status,
                    po.payment_status,
                    f"{po.ordered_value:.2f}",
                    f"{po.total_value:.2f}",
                    f"{po.amount_paid:.2f}",
                    f"{po.balance_due:.2f}",
                    po.vendor_invoice_number or "",
                    po.created_at.isoformat() if po.created_at else "",
                ]
            )
        return response

    @action(detail=True, methods=["post"], url_path="cancel")
    def cancel(self, request, *args, **kwargs):
        purchase = self.get_object()
        serializer = PurchaseCancelSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        cancelled = cancel_purchase_order(
            purchase=purchase,
            actor=request.user,
            reason=serializer.validated_data.get("reason", ""),
            audit_context=AuditContext.from_request(request),
        )
        output = PurchaseSerializer(cancelled, context=self.get_serializer_context())
        return Response(output.data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="receive")
    def receive(self, request, *args, **kwargs):
        purchase = self.get_object()
        serializer = PurchaseReceiveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            updated_purchase = receive_purchase_order(
                purchase=purchase,
                items_received=serializer.validated_data["items_received"],
                user=request.user,
                audit_context=AuditContext.from_request(request),
            )
            data = {
                "status": "received",
                "purchase_status": updated_purchase.status,
                "items_received_count": len(serializer.validated_data["items_received"]),
            }
            return Response(data, status=status.HTTP_200_OK)
        except BusinessRuleError:
            raise
        except Exception as exc:
            return Response(
                {"code": "RECEIVE_ERROR", "detail": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )

    @action(detail=True, methods=["get", "post"], url_path="purchase-invoice")
    def purchase_invoice(self, request, *args, **kwargs):
        purchase = self.get_object()
        if request.method == "GET":
            invoices = (
                PurchaseInvoice.objects.for_organization(self.organization)
                .filter(purchase=purchase)
                .with_details()
            )
            serializer = PurchaseInvoiceSerializer(invoices, many=True, context=self.get_serializer_context())
            return Response(serializer.data, status=status.HTTP_200_OK)

        serializer = PurchaseInvoiceCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        validated = dict(serializer.validated_data)
        items_list = validated.pop("items", None)
        inv = record_purchase_invoice(
            purchase=purchase,
            actor=request.user,
            payload=PurchaseInvoiceInput(lines=items_list, **validated),
            audit_context=AuditContext.from_request(request),
        )
        output = PurchaseInvoiceSerializer(inv, context=self.get_serializer_context())
        return Response(output.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get", "post"], url_path="payments")
    def payments(self, request, *args, **kwargs):
        purchase = self.get_object()
        if request.method == "GET":
            payments_qs = (
                PurchasePayment.objects.filter(purchase=purchase)
                .select_related("payment", "recorded_by")
                .order_by("-created_at")
            )
            serializer = PurchasePaymentSerializer(
                payments_qs,
                many=True,
                context=self.get_serializer_context(),
            )
            return Response(serializer.data, status=status.HTTP_200_OK)

        serializer = PurchasePaymentCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        purchase_payment = record_supplier_payment(
            purchase=purchase,
            actor=request.user,
            payload=PurchasePaymentInput(**serializer.validated_data),
            audit_context=AuditContext.from_request(request),
        )
        output = PurchasePaymentSerializer(purchase_payment, context=self.get_serializer_context())
        return Response(output.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get", "post"], url_path="returns")
    def returns(self, request, *args, **kwargs):
        purchase = self.get_object()
        if request.method == "GET":
            returns_qs = (
                PurchaseReturn.objects.for_organization(self.organization)
                .filter(purchase=purchase)
                .with_details()
            )
            serializer = PurchaseReturnSerializer(
                returns_qs,
                many=True,
                context=self.get_serializer_context(),
            )
            return Response(serializer.data, status=status.HTTP_200_OK)

        serializer = PurchaseReturnCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        raw_items = serializer.validated_data["items"]
        resolved_items = []
        for row in raw_items:
            batch_obj = None
            if row.get("batch"):
                batch_obj = ProductBatch.objects.filter(pk=row["batch"]).first()
            resolved_items.append(
                {
                    "purchase_item": row["purchase_item"],
                    "quantity": row["quantity"],
                    "batch": batch_obj,
                }
            )
        purchase_return = create_purchase_return(
            purchase=purchase,
            actor=request.user,
            items=resolved_items,
            reason=serializer.validated_data.get("reason", ""),
            audit_context=AuditContext.from_request(request),
        )
        output = PurchaseReturnSerializer(purchase_return, context=self.get_serializer_context())
        return Response(output.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get"], url_path="stock-activity")
    def stock_activity(self, request, *args, **kwargs):
        purchase = self.get_object()
        entries = get_purchase_stock_activity(purchase)
        serializer = PurchaseStockActivitySerializer(
            entries,
            many=True,
            context=self.get_serializer_context(),
        )
        return Response(serializer.data, status=status.HTTP_200_OK)
