from __future__ import annotations

import uuid
from datetime import date

from rest_framework import mixins
from rest_framework import status
from rest_framework import viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.filters import OrderingFilter
from rest_framework.filters import SearchFilter
from rest_framework.response import Response

from core.applications.notification.audit.context import AuditContext
from core.applications.payment.api.permissions import PaymentPermission
from core.applications.payment.api.schemas import payment_schema
from core.applications.payment.api.serializers import PaymentCreateSerializer
from core.applications.payment.api.serializers import PaymentSerializer
from core.applications.payment.api.serializers import RecordedPaymentSerializer
from core.applications.payment.models import InvoicePayment
from core.applications.payment.services.receipts import ReceivePaymentInput
from core.applications.payment.services.receipts import receive_invoice_payment
from core.applications.users.permissions import OrganizationScopedMixin
from core.helper.enums import PaymentMethod
from core.helper.pagination import CustomPagination


@payment_schema
class PaymentViewSet(
    OrganizationScopedMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """
    Customer payments (money received against invoices). Each one is also the
    receipt. Payments are never edited or deleted (PRD §44).
    """

    permission_classes = [PaymentPermission]
    serializer_class = PaymentSerializer
    pagination_class = CustomPagination
    http_method_names = ["get", "post", "head", "options"]
    filter_backends = [SearchFilter, OrderingFilter]
    search_fields = [
        "receipt_number",
        "payment__reference",
        "invoice__invoice_number",
        "invoice__customer__name",
    ]
    ordering_fields = ["received_on", "amount"]
    ordering = ["-received_on"]
    queryset = InvoicePayment.objects.none()

    def get_queryset(self):
        if not self.request.user.is_authenticated or not self.organization:
            return InvoicePayment.objects.none()
        queryset = InvoicePayment.objects.filter(invoice__organization=self.organization).select_related(
            "payment", "sales_rep", "invoice__customer", "invoice__warehouse"
        )
        queryset = _apply_filters(queryset, self.request.query_params)
        return self.scope_to_locations(queryset, field="invoice__warehouse")

    def create(self, request, *args, **kwargs):
        serializer = PaymentCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = receive_invoice_payment(
            organization=self.organization,
            actor=request.user,
            data=ReceivePaymentInput(**serializer.validated_data),
            location_scope=self.location_scope,
            audit_context=AuditContext.from_request(request),
        )
        # Re-read so the response shows the invoice's new status and balance.
        receipt = InvoicePayment.objects.select_related(
            "payment", "sales_rep", "invoice__customer", "invoice__warehouse"
        ).get(pk=result.receipt.pk)
        receipt.change_due = result.change_due
        receipt.replayed = result.replayed
        data = RecordedPaymentSerializer(receipt, context=self.get_serializer_context()).data
        return Response(data, status=status.HTTP_200_OK if result.replayed else status.HTTP_201_CREATED)


def _apply_filters(queryset, params):
    for name, lookup in (("invoice", "invoice_id"), ("customer", "invoice__customer_id")):
        raw = params.get(name)
        if raw:
            try:
                queryset = queryset.filter(**{lookup: uuid.UUID(raw.strip())})
            except ValueError:
                raise ValidationError({name: ["Must be a valid id."]}) from None

    method = (params.get("method") or "").strip().lower()
    if method:
        if method not in PaymentMethod.values:
            raise ValidationError({"method": [f"Must be one of: {', '.join(PaymentMethod.values)}."]})
        queryset = queryset.filter(payment__method=method)

    date_from, date_to = _date(params, "date_from"), _date(params, "date_to")
    if date_from and date_to and date_from > date_to:
        raise ValidationError({"date_to": ["Must be on or after date_from."]})
    if date_from:
        queryset = queryset.filter(received_on__date__gte=date_from)
    if date_to:
        queryset = queryset.filter(received_on__date__lte=date_to)
    return queryset


def _date(params, name: str) -> date | None:
    raw = params.get(name)
    if not raw:
        return None
    try:
        return date.fromisoformat(raw.strip())
    except ValueError:
        raise ValidationError({name: ["Use YYYY-MM-DD."]}) from None
