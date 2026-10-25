from __future__ import annotations

import csv
from collections.abc import Iterator
from functools import cached_property
from typing import Any
from typing import ClassVar

from django.db import transaction
from django.http import StreamingHttpResponse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter
from drf_spectacular.utils import OpenApiResponse
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from core.applications.invoice.api.serializers.invoice_serializers import (
    AuditCursorField,
)
from core.applications.invoice.api.serializers.invoice_serializers import (
    AuditTrailPageSerializer,
)
from core.applications.invoice.api.serializers.invoice_serializers import (
    AuditTrailQuerySerializer,
)
from core.applications.invoice.api.serializers.invoice_serializers import (
    InvoiceArchiveSerializer,
)
from core.applications.invoice.api.serializers.invoice_serializers import (
    InvoiceConfirmSerializer,
)
from core.applications.invoice.api.serializers.invoice_serializers import (
    InvoiceCreateSerializer,
)
from core.applications.invoice.api.serializers.invoice_serializers import (
    InvoiceDetailSerializer,
)
from core.applications.invoice.api.serializers.invoice_serializers import (
    InvoiceListFilterSerializer,
)
from core.applications.invoice.api.serializers.invoice_serializers import (
    InvoiceListSerializer,
)
from core.applications.invoice.api.serializers.invoice_serializers import (
    InvoiceStateSerializer,
)
from core.applications.invoice.api.serializers.invoice_serializers import (
    InvoiceSummarySerializer,
)
from core.applications.invoice.api.serializers.invoice_serializers import (
    InvoiceUnarchiveSerializer,
)
from core.applications.invoice.api.serializers.invoice_serializers import (
    InvoiceUpdateSerializer,
)
from core.applications.invoice.api.serializers.invoice_serializers import (
    InvoiceVoidAndCorrectSerializer,
)
from core.applications.invoice.api.serializers.invoice_serializers import (
    InvoiceVoidSerializer,
)
from core.applications.invoice.api.serializers.invoice_serializers import (
    get_idempotency_key,
)
from core.applications.invoice.api.serializers.invoice_serializers import (
    invoice_context,
)
from core.applications.invoice.models import Invoice
from core.applications.invoice.services import invoices as invoice_services
from core.applications.invoice.services.ports import PERM_AUDIT_VIEW
from core.applications.invoice.services.ports import PERM_INVOICE_ARCHIVE
from core.applications.invoice.services.ports import PERM_INVOICE_CONFIRM
from core.applications.invoice.services.ports import PERM_INVOICE_CREATE
from core.applications.invoice.services.ports import PERM_INVOICE_VIEW
from core.applications.invoice.services.ports import PERM_INVOICE_VOID
from core.applications.notification.audit.context import AuditContext
from core.applications.users import services as user_services
from core.applications.users.permissions import HasOrgPermission
from core.applications.users.permissions import OrganizationScopedMixin
from core.helper.custom_exceptions import CustomError
from core.helper.custom_exceptions import get_all_schema
from core.helper.pagination import CustomPagination

__all__ = ["InvoiceViewSet"]

# Permissions `available_actions` looks at, resolved once per request.
_ACTION_PERMISSIONS = (
    PERM_INVOICE_CREATE,
    PERM_INVOICE_CONFIRM,
    PERM_INVOICE_VOID,
    PERM_INVOICE_ARCHIVE,
)

# A bigger export belongs in a background job (PRD §51), not in a request.
MAX_EXPORT_ROWS = 50_000
EXPORT_CHUNK_SIZE = 200
EXPORT_COLUMNS = (
    "Invoice",
    "Status",
    "Customer",
    "Issued",
    "Due",
    "Currency",
    "Total",
    "Paid",
    "Balance",
)

_IDEMPOTENCY_HEADER = OpenApiParameter(
    "Idempotency-Key",
    OpenApiTypes.STR,
    OpenApiParameter.HEADER,
    required=False,
    description="Repeating a request with the same key returns the first result.",
)
_BUSINESS_RULE_RESPONSE = OpenApiResponse(
    description=(
        "A business rule blocked the action. `code` is one of "
        "INVALID_INVOICE_TRANSITION, INVOICE_HAS_PAYMENTS, INSUFFICIENT_STOCK, "
        "CREDIT_LIMIT_EXCEEDED, INVOICE_ALREADY_ISSUED_FOR_SALE, APPROVAL_REQUIRED."
    ),
)


def _responses(
    status_code: int,
    serializer: Any,
    description: str,
    *,
    business_rules: bool = False,
) -> dict[int, Any]:
    """Documented responses: the project's generic errors plus the success body."""
    documented: dict[int, Any] = {
        **CustomError.DEFAULT_ERROR_SCHEMA(),
        status_code: OpenApiResponse(response=serializer, description=description),
    }
    if business_rules:
        documented[status.HTTP_409_CONFLICT] = _BUSINESS_RULE_RESPONSE
    return documented


class _Echo:
    """A write-only buffer: `csv.writer` hands each row straight back to us."""

    def write(self, value: str) -> str:
        return value


def _csv_safe(value: Any) -> str:
    """
    Neutralise spreadsheet formula injection: a customer name such as
    `=HYPERLINK(...)` must open as text in Excel, not run.
    """
    text = "" if value is None else str(value)
    return f"'{text}" if text[:1] in {"=", "+", "-", "@", "\t", "\r"} else text


class InvoiceViewSet(OrganizationScopedMixin, viewsets.GenericViewSet):
    """
    Invoices of the organization in the URL.

    The organization comes from the URL and is verified against the caller's
    active membership by `HasOrgPermission`. It is never read from the body.
    """

    pagination_class = CustomPagination
    http_method_names = ("get", "post", "put", "head", "options")
    # Used for schema generation only. Real querysets come from the services,
    # which scope every query to the organization.
    queryset = Invoice.objects.none()

    # Recording the payment taken at confirmation needs the payments service.
    # Until it is wired, a request that includes a payment is refused up front
    # (nothing is confirmed) instead of confirming without recording the money.
    payments_enabled = False

    # -- per-action permissions (the permission codes are the ports' own) ---- #
    action_permissions: ClassVar = {
        "list": HasOrgPermission(PERM_INVOICE_VIEW),
        "summary": HasOrgPermission(PERM_INVOICE_VIEW),
        "export": HasOrgPermission(PERM_INVOICE_VIEW),
        "retrieve": HasOrgPermission(PERM_INVOICE_VIEW),
        "create": HasOrgPermission(PERM_INVOICE_CREATE),
        "update": HasOrgPermission(PERM_INVOICE_CREATE),
        "confirm": HasOrgPermission(PERM_INVOICE_CONFIRM),
        "void": HasOrgPermission(PERM_INVOICE_VOID),
        "void_and_correct": HasOrgPermission(PERM_INVOICE_VOID),
        "archive": HasOrgPermission(PERM_INVOICE_ARCHIVE),
        "unarchive": HasOrgPermission(PERM_INVOICE_ARCHIVE),
        "audit": HasOrgPermission(PERM_AUDIT_VIEW),
    }
    default_permission = HasOrgPermission(PERM_INVOICE_VIEW)

    serializer_classes: ClassVar = {
        "list": InvoiceListSerializer,
        "retrieve": InvoiceDetailSerializer,
        "create": InvoiceCreateSerializer,
        "update": InvoiceUpdateSerializer,
        "confirm": InvoiceConfirmSerializer,
        "void": InvoiceVoidSerializer,
        "void_and_correct": InvoiceVoidAndCorrectSerializer,
        "archive": InvoiceArchiveSerializer,
        "unarchive": InvoiceUnarchiveSerializer,
        "summary": InvoiceSummarySerializer,
        "audit": AuditTrailPageSerializer,
        "export": InvoiceListFilterSerializer,
    }

    # ------------------------------------------------------------------ #
    # Request context
    # ------------------------------------------------------------------ #

    def get_permissions(self):
        permission = self.action_permissions.get(self.action, self.default_permission)
        return [IsAuthenticated(), permission()]

    @cached_property
    def today(self):
        """The organization's own calendar date, as the services see it."""
        return invoice_services._today(self.organization)  # noqa: SLF001

    @cached_property
    def permission_codes(self) -> frozenset[str]:
        """
        Which action permissions the caller holds, for `available_actions`.
        Asked of the users service, the same source `HasOrgPermission` uses,
        and resolved once per request.
        """
        return frozenset(
            code
            for code in _ACTION_PERMISSIONS
            if user_services.has_org_permission(self.request.user, self.organization, code)
        )

    def _audit_context(self) -> Any:
        """Request metadata (IP, device) for the audit trail."""
        return AuditContext.from_request(self.request)

    def get_serializer_class(self):
        return self.serializer_classes.get(self.action, InvoiceListSerializer)

    def get_serializer_context(self) -> dict[str, Any]:
        context = super().get_serializer_context()
        if getattr(self, "swagger_fake_view", False):
            return context
        return invoice_context(
            permissions=self.permission_codes,
            today=self.today,
            organization=self.organization,
            actor_membership=self.membership,
            **context,
        )

    def _body(self, **context: Any):
        """The validated request body for the current action."""
        serializer = self.get_serializer_class()(
            data=self.request.data,
            context={**self.get_serializer_context(), **context},
        )
        serializer.is_valid(raise_exception=True)
        return serializer

    def _query(self, serializer_class: Any):
        """Validated query parameters."""
        serializer = serializer_class(
            data=self.request.query_params,
            context=self.get_serializer_context(),
        )
        serializer.is_valid(raise_exception=True)
        return serializer

    def _service_args(self) -> dict[str, Any]:
        return {"actor": self.request.user, "audit_context": self._audit_context()}

    def _detail(self, invoice: Any, http_status: int = status.HTTP_200_OK) -> Response:
        """
        The full detail of an invoice a command has just changed. The caller
        was authorized for that command, so the reload is scoped to the
        organization but not gated on the separate view permission.
        """
        loaded = Invoice.objects.for_organization(self.organization).for_detail().get(pk=invoice.pk)
        data = InvoiceDetailSerializer(loaded, context=self.get_serializer_context()).data
        return Response(data, status=http_status)

    def _state(self, invoice: Any) -> Response:
        data = InvoiceStateSerializer(invoice, context=self.get_serializer_context()).data
        return Response(data)

    # ------------------------------------------------------------------ #
    # List page
    # ------------------------------------------------------------------ #

    @extend_schema(
        summary="List invoices",
        parameters=[InvoiceListFilterSerializer],
        responses=get_all_schema("invoices", InvoiceListSerializer),
    )
    def list(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        params = self._query(InvoiceListFilterSerializer)
        queryset = invoice_services.invoice_queryset(
            self.organization,
            actor=request.user,
            filters=params.to_filters(),
            ordering=params.get_ordering(),
        )
        page = self.paginate_queryset(queryset)
        serializer = InvoiceListSerializer(
            page,
            many=True,
            context=self.get_serializer_context(),
        )
        return self.get_paginated_response(serializer.data)

    @extend_schema(
        summary="Summary cards for the invoice list",
        description=(
            "Takes the same filters as the list, so the cards always match the "
            "rows. Drafts and voided invoices are counted apart and never "
            "inflate a total."
        ),
        parameters=[InvoiceListFilterSerializer],
        responses=_responses(status.HTTP_200_OK, InvoiceSummarySerializer, "Totals."),
    )
    @action(detail=False, methods=["get"])
    def summary(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        params = self._query(InvoiceListFilterSerializer)
        data = invoice_services.get_invoice_summary(
            self.organization,
            actor=request.user,
            filters=params.to_filters(),
        )
        return Response(InvoiceSummarySerializer(data).data)

    @extend_schema(
        summary="Export the filtered invoices as CSV",
        description=f"Same filters as the list. Refused above {MAX_EXPORT_ROWS} rows: narrow the filters.",
        parameters=[InvoiceListFilterSerializer],
        responses={
            **CustomError.DEFAULT_ERROR_SCHEMA(),
            status.HTTP_200_OK: OpenApiResponse(response=OpenApiTypes.BINARY, description="text/csv"),
        },
    )
    @action(detail=False, methods=["get"])
    def export(self, request: Request, *args: Any, **kwargs: Any) -> StreamingHttpResponse:
        filters = self._query(InvoiceListFilterSerializer).to_filters()
        matching = invoice_services.invoice_queryset(
            self.organization,
            actor=request.user,
            filters=filters,
        ).count()
        if matching > MAX_EXPORT_ROWS:
            msg = f"Too many invoices to export ({matching}). Narrow the filters to {MAX_EXPORT_ROWS} or fewer."
            raise CustomError.BadRequest(msg)

        response = StreamingHttpResponse(
            self._csv_rows(filters),
            content_type="text/csv; charset=utf-8",
        )
        response["Content-Disposition"] = f'attachment; filename="invoices-{self.today:%Y%m%d}.csv"'
        return response

    def _csv_rows(self, filters: Any) -> Iterator[str]:
        """Walk the whole result with keyset pages, so memory stays flat."""
        writer = csv.writer(_Echo())
        yield writer.writerow(EXPORT_COLUMNS)
        cursor = None
        while True:
            page = invoice_services.list_invoices(
                self.organization,
                actor=self.request.user,
                filters=filters,
                cursor=cursor,
                limit=EXPORT_CHUNK_SIZE,
            )
            if not page:
                return
            for invoice in page:
                yield writer.writerow(
                    [
                        _csv_safe(invoice.invoice_number),
                        _csv_safe(invoice.get_status_display()),
                        _csv_safe(invoice.customer_name),
                        invoice.issued_at.isoformat() if invoice.issued_at else "",
                        invoice.due_date.isoformat(),
                        invoice.currency,
                        invoice.total,
                        invoice.amount_paid,
                        invoice.balance_due,
                    ]
                )
            last = page[-1]
            cursor = (last.created_at, last.pk)

    # ------------------------------------------------------------------ #
    # Detail page
    # ------------------------------------------------------------------ #

    @extend_schema(
        summary="Invoice detail",
        responses=_responses(status.HTTP_200_OK, InvoiceDetailSerializer, "The invoice."),
    )
    def retrieve(self, request: Request, pk: str | None = None, *args: Any, **kwargs: Any) -> Response:
        invoice = invoice_services.get_invoice(self.organization, pk, actor=request.user)
        return Response(InvoiceDetailSerializer(invoice, context=self.get_serializer_context()).data)

    @extend_schema(
        summary="Audit trail of an invoice",
        description="Newest first. Needs audit access, which is separate from invoice access.",
        parameters=[AuditTrailQuerySerializer],
        responses=_responses(status.HTTP_200_OK, AuditTrailPageSerializer, "One page of audit events."),
    )
    @action(detail=True, methods=["get"])
    def audit(self, request: Request, pk: str | None = None, *args: Any, **kwargs: Any) -> Response:
        query = self._query(AuditTrailQuerySerializer).validated_data
        limit = query["limit"]
        # One extra row tells us whether another page exists.
        rows = invoice_services.get_invoice_audit_trail(
            self.organization,
            pk,
            actor=request.user,
            cursor=query.get("cursor"),
            limit=limit + 1,
        )
        has_more = len(rows) > limit
        rows = rows[:limit]
        next_cursor = AuditCursorField.encode(rows[-1].created_at, rows[-1].pk) if has_more else None
        page = AuditTrailPageSerializer(
            {"results": rows, "next_cursor": next_cursor},
            context=self.get_serializer_context(),
        )
        return Response(page.data)

    # ------------------------------------------------------------------ #
    # Create, edit, confirm
    # ------------------------------------------------------------------ #

    @extend_schema(
        summary="Create a standalone Draft invoice",
        request=InvoiceCreateSerializer,
        parameters=[_IDEMPOTENCY_HEADER],
        responses=_responses(status.HTTP_201_CREATED, InvoiceDetailSerializer, "The Draft.", business_rules=True),
    )
    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        serializer = self._body(idempotency_key=get_idempotency_key(request))
        invoice = invoice_services.create_invoice(
            self.organization,
            **self._service_args(),
            **serializer.service_kwargs(),
        )
        return self._detail(invoice, status.HTTP_201_CREATED)

    @extend_schema(
        summary="Replace the lines of a Draft",
        request=InvoiceUpdateSerializer,
        responses=_responses(status.HTTP_200_OK, InvoiceDetailSerializer, "The re-priced Draft.", business_rules=True),
    )
    def update(self, request: Request, pk: str | None = None, *args: Any, **kwargs: Any) -> Response:
        serializer = self._body()
        invoice = invoice_services.update_draft_invoice(
            self.organization,
            pk,
            **self._service_args(),
            **serializer.service_kwargs(),
        )
        return self._detail(invoice)

    @extend_schema(
        summary="Confirm a Draft",
        description=(
            "Locks the customer, checks credit, deducts stock and freezes the "
            "totals, atomically. An optional `payment` is the money taken now."
        ),
        request=InvoiceConfirmSerializer,
        responses=_responses(status.HTTP_200_OK, InvoiceDetailSerializer, "The confirmed invoice.", business_rules=True),
    )
    @action(detail=True, methods=["post"])
    def confirm(self, request: Request, pk: str | None = None, *args: Any, **kwargs: Any) -> Response:
        serializer = self._body()
        payment = serializer.payment_details()
        if payment is not None and not self.payments_enabled:
            msg = "Recording a payment at confirmation is not available yet."
            raise CustomError.ServiceUnavailable(msg)

        # The invoice and its payment commit together or not at all (PRD §42).
        with transaction.atomic():
            invoice = invoice_services.confirm_invoice(
                self.organization,
                pk,
                **self._service_args(),
                **serializer.service_kwargs(),
            )
            if payment is not None:
                self.record_upfront_payment(invoice, payment)
        return self._detail(invoice)

    def record_upfront_payment(self, invoice: Any, payment: dict[str, Any]) -> None:
        """
        Record the payment taken at confirmation through the payments service,
        inside the confirmation's transaction. Implement this, then set
        `payments_enabled = True`.
        """
        msg = "Wire the payments service here, then set payments_enabled = True."
        raise NotImplementedError(msg)

    # ------------------------------------------------------------------ #
    # Void and correct
    # ------------------------------------------------------------------ #

    @extend_schema(
        summary="Void an invoice",
        description="The record stays forever. An invoice with payments cannot be voided.",
        request=InvoiceVoidSerializer,
        responses=_responses(status.HTTP_200_OK, InvoiceDetailSerializer, "The voided invoice.", business_rules=True),
    )
    @action(detail=True, methods=["post"])
    def void(self, request: Request, pk: str | None = None, *args: Any, **kwargs: Any) -> Response:
        serializer = self._body()
        invoice = invoice_services.void_invoice(
            self.organization,
            pk,
            **self._service_args(),
            **serializer.service_kwargs(),
        )
        return self._detail(invoice)

    @extend_schema(
        summary="Void an invoice and open a corrected Draft",
        description="Both steps commit together. The response is the corrected Draft, which links back through `replaces`.",
        request=InvoiceVoidAndCorrectSerializer,
        responses=_responses(
            status.HTTP_201_CREATED,
            InvoiceDetailSerializer,
            "The corrected Draft.",
            business_rules=True,
        ),
    )
    @action(detail=True, methods=["post"], url_path="void-and-correct")
    def void_and_correct(self, request: Request, pk: str | None = None, *args: Any, **kwargs: Any) -> Response:
        serializer = self._body()
        corrected = invoice_services.void_and_correct(
            self.organization,
            pk,
            **self._service_args(),
            **serializer.service_kwargs(),
        )
        return self._detail(corrected, status.HTTP_201_CREATED)

    # ------------------------------------------------------------------ #
    # Archive
    # ------------------------------------------------------------------ #

    @extend_schema(
        summary="Archive a settled invoice",
        description="Only Paid and voided invoices. Archiving is idempotent and never changes money or stock.",
        request=InvoiceArchiveSerializer,
        responses=_responses(status.HTTP_200_OK, InvoiceStateSerializer, "The invoice's new state.", business_rules=True),
    )
    @action(detail=True, methods=["post"])
    def archive(self, request: Request, pk: str | None = None, *args: Any, **kwargs: Any) -> Response:
        serializer = self._body()
        invoice = invoice_services.archive_invoice(
            self.organization,
            pk,
            **self._service_args(),
            **serializer.service_kwargs(),
        )
        return self._state(invoice)

    @extend_schema(
        summary="Bring an archived invoice back",
        request=InvoiceUnarchiveSerializer,
        responses=_responses(status.HTTP_200_OK, InvoiceStateSerializer, "The invoice's new state."),
    )
    @action(detail=True, methods=["post"])
    def unarchive(self, request: Request, pk: str | None = None, *args: Any, **kwargs: Any) -> Response:
        serializer = self._body()
        invoice = invoice_services.unarchive_invoice(
            self.organization,
            pk,
            **self._service_args(),
            **serializer.service_kwargs(),
        )
        return self._state(invoice)
