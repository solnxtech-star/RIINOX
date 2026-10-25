from __future__ import annotations

import csv
from collections.abc import Iterator
from functools import cached_property
from typing import Any
from typing import ClassVar

from django.db import transaction
from django.http import StreamingHttpResponse
from rest_framework import status
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from core.applications.invoice.api.constants import EXPORT_CHUNK_SIZE
from core.applications.invoice.api.constants import EXPORT_COLUMNS
from core.applications.invoice.api.constants import MAX_EXPORT_ROWS
from core.applications.invoice.api.schemas.invoice_schemas import invoice_viewset_schema
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
from core.helper.pagination import CustomPagination

__all__ = ["InvoiceViewSet"]

# Permissions `available_actions` looks at, resolved once per request.
_ACTION_PERMISSIONS = (
    PERM_INVOICE_CREATE,
    PERM_INVOICE_CONFIRM,
    PERM_INVOICE_VOID,
    PERM_INVOICE_ARCHIVE,
)


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


@invoice_viewset_schema
class InvoiceViewSet(OrganizationScopedMixin, viewsets.GenericViewSet):
    """
    Invoices of the organization in the URL.

    The organization comes from the URL and is verified against the caller's
    active membership by `HasOrgPermission`. It is never read from the body.

    This class holds behaviour only. Its OpenAPI documentation (what each
    endpoint does, its rules and its responses) lives in
    `api/schemas/invoice_schemas.py` and is applied by `invoice_viewset_schema`.
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

    @action(detail=False, methods=["get"])
    def summary(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        params = self._query(InvoiceListFilterSerializer)
        data = invoice_services.get_invoice_summary(
            self.organization,
            actor=request.user,
            filters=params.to_filters(),
        )
        return Response(InvoiceSummarySerializer(data).data)

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

    def retrieve(self, request: Request, pk: str | None = None, *args: Any, **kwargs: Any) -> Response:
        invoice = invoice_services.get_invoice(self.organization, pk, actor=request.user)
        return Response(InvoiceDetailSerializer(invoice, context=self.get_serializer_context()).data)

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

    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        serializer = self._body(idempotency_key=get_idempotency_key(request))
        invoice = invoice_services.create_invoice(
            self.organization,
            **self._service_args(),
            **serializer.service_kwargs(),
        )
        return self._detail(invoice, status.HTTP_201_CREATED)

    def update(self, request: Request, pk: str | None = None, *args: Any, **kwargs: Any) -> Response:
        serializer = self._body()
        invoice = invoice_services.update_draft_invoice(
            self.organization,
            pk,
            **self._service_args(),
            **serializer.service_kwargs(),
        )
        return self._detail(invoice)

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
