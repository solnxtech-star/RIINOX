from __future__ import annotations

import base64
import binascii
import uuid
from collections.abc import Collection
from collections.abc import Mapping
from datetime import date
from datetime import datetime
from decimal import Decimal
from typing import Any

from django.utils import timezone
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from core.applications.invoice.queryset import ORDERING_FIELDS
from core.applications.invoice.queryset import OUTSTANDING_STATUSES
from core.applications.invoice.services.invoices import MAX_IDEMPOTENCY_KEY_LENGTH
from core.applications.invoice.services.invoices import MAX_INVOICE_LINES
from core.applications.invoice.services.invoices import MAX_LINE_QUANTITY
from core.applications.invoice.services.invoices import MIN_VOID_REASON_LENGTH
from core.applications.invoice.services.invoices import InvoiceFilters
from core.applications.invoice.services.invoices import InvoiceLineInput
from core.applications.invoice.services.invoices import available_actions
from core.helper.enums import InvoiceStatusChoices

__all__ = [
    "AuditCursorField",
    "AuditLogSerializer",
    "AuditTrailPageSerializer",
    "AuditTrailQuerySerializer",
    "InvoiceArchiveSerializer",
    "InvoiceConfirmSerializer",
    "InvoiceCreateSerializer",
    "InvoiceDetailSerializer",
    "InvoiceItemSerializer",
    "InvoiceLineSerializer",
    "InvoiceListFilterSerializer",
    "InvoiceListSerializer",
    "InvoiceRefSerializer",
    "InvoiceStateSerializer",
    "InvoiceSummarySerializer",
    "InvoiceUnarchiveSerializer",
    "InvoiceUpdateSerializer",
    "InvoiceVoidAndCorrectSerializer",
    "InvoiceVoidSerializer",
    "PaymentInputSerializer",
    "ServiceInputSerializer",
    "UserRefSerializer",
    "WarehouseRefSerializer",
    "get_idempotency_key",
    "invoice_context",
]

# --------------------------------------------------------------------------- #
# Limits
# --------------------------------------------------------------------------- #

MAX_NOTES_LENGTH = 2000
MAX_REASON_LENGTH = 500
MAX_SEARCH_LENGTH = 100
MAX_LINE_TEXT_LENGTH = 255
MAX_UNIT_NAME_LENGTH = 50
MAX_PAYMENT_REFERENCE_LENGTH = 100
MAX_PAYMENT_METHOD_LENGTH = 50
MIN_PAYMENT_AMOUNT = Decimal("0.01")
CURRENCY_CODE_LENGTH = 3

ARCHIVE_SCOPES = ("active", "archived", "all")
ORDERING_CHOICES = [*sorted(ORDERING_FIELDS), *(f"-{name}" for name in sorted(ORDERING_FIELDS))]

MONEY: dict[str, Any] = {"max_digits": 14, "decimal_places": 2}
CENT = Decimal("0.01")

# Fields a client must never send. They get a pointed message instead of the
# generic "unknown field" one (PRD §39: the backend owns pricing and totals).
_SERVER_OWNED_FIELDS = frozenset(
    {
        "amount_paid",
        "balance_due",
        "price",
        "status",
        "subtotal",
        "tax",
        "tax_amount",
        "tax_rate",
        "total",
        "unit_multiplier",
        "unit_price",
    }
)


# =========================================================================== #
# 1. Fields and bases
# =========================================================================== #


class IdField(serializers.CharField):
    """
    A primary key that may be an integer or a UUID.

    It is trimmed, length-bounded, and a UUID is rewritten to its canonical
    dashed lowercase form, so the same id sent as `15a9...`, `15A9...` or
    `15a9423c-e483-...` is always the same value to the services. Anything
    that is not a UUID is passed through untouched. The services treat a
    malformed or cross-tenant id as "not found", so a client can never tell
    the two apart.
    """

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("max_length", 64)
        kwargs.setdefault("trim_whitespace", True)
        super().__init__(**kwargs)

    def to_internal_value(self, data: Any) -> str:
        value = super().to_internal_value(data)
        try:
            return str(uuid.UUID(value))
        except ValueError:
            return value

    def to_representation(self, value: Any) -> str:
        return str(value)


class MoneyInputField(serializers.DecimalField):
    """
    Non-negative money with two decimals, as accepted from a client. The bounds
    match the database column, so an oversized amount is a clean 400 instead of
    a database error.
    """

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("max_digits", 12)
        kwargs.setdefault("decimal_places", 2)
        kwargs.setdefault("min_value", 0)
        super().__init__(**kwargs)


class CurrencyField(serializers.CharField):
    """
    A three-letter currency code, upper-cased. Blank or null means "not
    given"; the create serializer turns that into None.
    """

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("required", False)
        kwargs.setdefault("allow_null", True)
        kwargs.setdefault("allow_blank", True)
        super().__init__(**kwargs)

    def to_internal_value(self, data: Any) -> str:
        code = super().to_internal_value(data).strip().upper()
        if len(code) != CURRENCY_CODE_LENGTH or not code.isalpha():
            msg = "A valid three-letter currency code is required."
            raise serializers.ValidationError(msg)
        return code


class CommaSeparatedListField(serializers.ListField):
    """Accepts `?status=paid,overdue` and `?status=paid&status=overdue` alike."""

    def get_value(self, dictionary: Any) -> Any:
        if hasattr(dictionary, "getlist"):
            raw = dictionary.getlist(self.field_name)
            if not raw:
                return serializers.empty
            parts: list[str] = []
            for item in raw:
                parts.extend(p.strip() for p in str(item).split(",") if p.strip())
            return parts
        return super().get_value(dictionary)


class StrictInputMixin:
    """
    Reject any key the serializer does not declare.

    DRF silently drops unknown keys, which is dangerous here: a client that
    sends `unit_price` believes it priced the line. Failing loudly keeps the
    contract honest and makes client bugs visible.
    """

    def to_internal_value(self, data: Any) -> Any:
        if isinstance(data, Mapping):
            writable = {name for name, field in self.fields.items() if not field.read_only}  # type: ignore[attr-defined]
            unknown = sorted(str(key) for key in data if str(key) not in writable)
            if unknown:
                raise serializers.ValidationError(
                    {
                        key: [
                            "This value is set by the server and cannot be sent."
                            if key in _SERVER_OWNED_FIELDS
                            else "Unknown field."
                        ]
                        for key in unknown
                    },
                    code="unknown_field",
                )
        return super().to_internal_value(data)  # type: ignore[misc]


class ServiceInputSerializer(StrictInputMixin, serializers.Serializer):
    """
    Base for request bodies consumed by the service layer. It cannot be saved,
    so a view can never write through it by accident.
    """

    def create(self, validated_data: dict[str, Any]) -> Any:
        msg = "Input serializers are consumed by services, never saved."
        raise NotImplementedError(msg)

    def update(self, instance: Any, validated_data: dict[str, Any]) -> Any:
        msg = "Input serializers are consumed by services, never saved."
        raise NotImplementedError(msg)

    def service_kwargs(self) -> dict[str, Any]:
        """
        Keyword arguments for the service call. Only keys the client actually
        sent (or that have a declared default) are present, so a service's own
        "keep the current value" defaults keep working for partial edits.
        """
        return dict(self.validated_data)


def get_idempotency_key(request: Any) -> str | None:
    """
    Read and validate the `Idempotency-Key` header (PRD §45). Returns None when
    absent; raises a field error when the key is too long.
    """
    key = (request.headers.get("Idempotency-Key") or "").strip()
    if not key:
        return None
    if len(key) > MAX_IDEMPOTENCY_KEY_LENGTH:
        msg = f"The Idempotency-Key header can have at most {MAX_IDEMPOTENCY_KEY_LENGTH} characters."
        raise serializers.ValidationError({"idempotency_key": [msg]})
    return key


def invoice_context(
    *,
    permissions: Collection[str] = (),
    today: date | None = None,
    idempotency_key: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """
    Build a serializer context with the keys documented above, so views never
    spell them by hand. DRF's own keys (`request`, `view`) go in `extra`.
    """
    context: dict[str, Any] = {"permissions": frozenset(permissions), **extra}
    if today is not None:
        context["today"] = today
    if idempotency_key:
        context["idempotency_key"] = idempotency_key
    return context


# =========================================================================== #
# 2. Input: commands
# =========================================================================== #


class InvoiceLineSerializer(StrictInputMixin, serializers.Serializer):
    """
    One line as a client may send it. There is no price, tax or multiplier
    field, and sending one is an error: the server prices every line from the
    product, the variant and the unit (through the units port).
    """

    product_id = IdField()
    quantity = serializers.IntegerField(min_value=1, max_value=MAX_LINE_QUANTITY)
    variant_id = IdField(required=False, allow_null=True, default=None)
    unit_name = serializers.CharField(
        required=False,
        allow_null=True,
        allow_blank=True,
        max_length=MAX_UNIT_NAME_LENGTH,
        default=None,
        help_text="Sell in a unit other than the base one, for example 'carton'.",
    )
    description = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=MAX_LINE_TEXT_LENGTH,
        default="",
        help_text="Optional custom line text. The product name is used when empty.",
    )


class LinesInputMixin(serializers.Serializer):
    """
    The shared `lines` field. After validation, `validated_data["lines"]` is a
    list of the services' `InvoiceLineInput` objects.
    """

    lines = InvoiceLineSerializer(many=True, allow_empty=False, max_length=MAX_INVOICE_LINES)

    def validate_lines(self, rows: list[dict[str, Any]]) -> list[InvoiceLineInput]:
        seen: set[tuple[str, str, str]] = set()
        errors: list[dict[str, list[str]]] = []
        lines: list[InvoiceLineInput] = []

        for row in rows:
            unit_name = (row.get("unit_name") or "").strip() or None
            variant_id = row.get("variant_id") or None
            key = (str(row["product_id"]), str(variant_id or ""), (unit_name or "").lower())

            if key in seen:
                msg = "A product, variant and unit combination can appear only once per invoice."
                errors.append({"non_field_errors": [msg]})
            else:
                errors.append({})
            seen.add(key)

            lines.append(
                InvoiceLineInput(
                    product_id=row["product_id"],
                    quantity=row["quantity"],
                    description=row.get("description", ""),
                    variant_id=variant_id,
                    unit_name=unit_name,
                )
            )

        if any(errors):
            raise serializers.ValidationError(errors)
        return lines


class ReasonInputMixin(serializers.Serializer):
    reason = serializers.CharField(
        min_length=MIN_VOID_REASON_LENGTH,
        max_length=MAX_REASON_LENGTH,
        trim_whitespace=True,
        error_messages={
            "min_length": f"A reason of at least {MIN_VOID_REASON_LENGTH} characters is required.",
            "blank": f"A reason of at least {MIN_VOID_REASON_LENGTH} characters is required.",
        },
    )


class InvoiceCreateSerializer(LinesInputMixin, ServiceInputSerializer):
    """
    Body of `create_invoice` (a standalone Draft).

    The idempotency key is not a body field: it travels in the
    `Idempotency-Key` header (PRD §45) and reaches this serializer through
    `context["idempotency_key"]`.
    """

    customer_id = IdField()
    warehouse_id = IdField()
    due_date = serializers.DateField(required=False, allow_null=True)
    discount = MoneyInputField(source="discount_amount", required=False, default=0)
    discount_approval_id = IdField(required=False, allow_null=True)
    notes = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
        max_length=MAX_NOTES_LENGTH,
    )
    extra_data = serializers.DictField(required=False)
    currency = CurrencyField(help_text="Defaults to the organization's currency.")

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if "currency" in attrs:
            attrs["currency"] = attrs["currency"] or None
        key = self.context.get("idempotency_key")
        if key:
            attrs["idempotency_key"] = key
        return attrs


class InvoiceUpdateSerializer(LinesInputMixin, ServiceInputSerializer):
    """
    Body of `update_draft_invoice`. `lines` is the full new set; any other
    field that is left out keeps its current value.
    """

    due_date = serializers.DateField(required=False, allow_null=True)
    discount = MoneyInputField(source="discount_amount", required=False)
    discount_approval_id = IdField(required=False, allow_null=True)
    notes = serializers.CharField(required=False, allow_blank=True, max_length=MAX_NOTES_LENGTH)


class PaymentInputSerializer(StrictInputMixin, serializers.Serializer):
    """
    The `payment` object of PRD §39. The method is organization-defined
    (PRD §26), so the payments service validates it; only the shape is checked
    here.
    """

    amount = MoneyInputField(min_value=MIN_PAYMENT_AMOUNT)
    method = serializers.CharField(max_length=MAX_PAYMENT_METHOD_LENGTH)
    reference = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=MAX_PAYMENT_REFERENCE_LENGTH,
    )
    paid_on = serializers.DateField(required=False)
    note = serializers.CharField(required=False, allow_blank=True, max_length=MAX_REASON_LENGTH)


class InvoiceConfirmSerializer(ServiceInputSerializer):
    """
    Body of `confirm_invoice`. An optional `payment` is the money taken at
    confirmation.

    `confirm_invoice` needs only its amount (to size the credit exposure) and
    never writes `amount_paid`. The view records the payment itself, in the
    same transaction, from `payment_details()`.
    """

    payment = PaymentInputSerializer(required=False, allow_null=True)
    credit_approval_id = IdField(required=False, allow_null=True)

    def payment_details(self) -> dict[str, Any] | None:
        """The validated `payment` object for the payments service, or None."""
        return self.validated_data.get("payment")

    def service_kwargs(self) -> dict[str, Any]:
        data = dict(self.validated_data)
        payment = data.pop("payment", None)
        data["upfront_payment"] = payment["amount"] if payment else Decimal(0)
        return data


class InvoiceVoidSerializer(ReasonInputMixin, ServiceInputSerializer):
    """Body of `void_invoice`."""

    approval_id = IdField(required=False, allow_null=True)


class InvoiceVoidAndCorrectSerializer(ReasonInputMixin, LinesInputMixin, ServiceInputSerializer):
    """
    Body of `void_and_correct`. `approval_id` covers the void;
    `discount_approval_id` covers any discount on the corrected invoice. A null
    `discount` keeps the original's.
    """

    due_date = serializers.DateField(required=False, allow_null=True)
    discount = MoneyInputField(source="discount_amount", required=False, allow_null=True)
    discount_approval_id = IdField(required=False, allow_null=True)
    notes = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
        max_length=MAX_NOTES_LENGTH,
    )
    approval_id = IdField(required=False, allow_null=True)


class InvoiceArchiveSerializer(ServiceInputSerializer):
    """Body of `archive_invoice`. The reason is optional and goes to the audit log."""

    reason = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=MAX_REASON_LENGTH,
        default="",
    )


class InvoiceUnarchiveSerializer(InvoiceArchiveSerializer):
    """Body of `unarchive_invoice`. Same shape as archiving."""


# =========================================================================== #
# 3. Input: list filters
# =========================================================================== #


class InvoiceListFilterSerializer(serializers.Serializer):
    """
    Query parameters of the invoice list (filter drawer and search box). The
    same parameters feed the summary cards, so the cards always match the rows.

    Unknown parameters such as `page` and `page_size` are ignored on purpose:
    the paginator owns them.

    Usage in a view::

        params = InvoiceListFilterSerializer(data=request.query_params)
        params.is_valid(raise_exception=True)
        qs = invoice_queryset(
            organization,
            actor=request.user,
            filters=params.to_filters(),
            ordering=params.get_ordering(),
        )
    """

    status = CommaSeparatedListField(
        child=serializers.ChoiceField(choices=InvoiceStatusChoices.choices),
        required=False,
        help_text="One or more statuses, comma-separated or repeated.",
    )
    customer = IdField(required=False)
    sales_rep = IdField(required=False)
    warehouse = IdField(required=False)
    search = serializers.CharField(required=False, allow_blank=True, max_length=MAX_SEARCH_LENGTH)
    issued_from = serializers.DateField(required=False)
    issued_to = serializers.DateField(required=False)
    due_from = serializers.DateField(required=False)
    due_to = serializers.DateField(required=False)
    overdue = serializers.BooleanField(required=False, default=False)
    archived = serializers.ChoiceField(choices=ARCHIVE_SCOPES, required=False, default="active")
    ordering = serializers.ChoiceField(choices=ORDERING_CHOICES, required=False)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        errors: dict[str, str] = {}
        for start, end in (("issued_from", "issued_to"), ("due_from", "due_to")):
            if start in attrs and end in attrs and attrs[start] > attrs[end]:
                errors[end] = f"`{end}` cannot be before `{start}`."
        if errors:
            raise serializers.ValidationError(errors)
        return attrs

    def get_ordering(self) -> str | None:
        """The validated ordering for `invoice_queryset`, or None for the default."""
        return self.validated_data.get("ordering")

    def to_filters(self) -> InvoiceFilters:
        data = self.validated_data
        return InvoiceFilters(
            statuses=tuple(data.get("status", ())),
            customer_id=data.get("customer"),
            sales_rep_id=data.get("sales_rep"),
            warehouse_id=data.get("warehouse"),
            search=data.get("search", ""),
            issued_from=data.get("issued_from"),
            issued_to=data.get("issued_to"),
            due_from=data.get("due_from"),
            due_to=data.get("due_to"),
            overdue_only=data.get("overdue", False),
            archived=data.get("archived", "active"),
        )


# =========================================================================== #
# 4. Output
# =========================================================================== #


class UserRefSerializer(serializers.Serializer):
    id = serializers.CharField(read_only=True)
    name = serializers.SerializerMethodField()
    email = serializers.CharField(read_only=True, default=None)

    @extend_schema_field(serializers.CharField())
    def get_name(self, user: Any) -> str:
        full_name = getattr(user, "get_full_name", None)
        name = full_name() if callable(full_name) else ""
        return name or getattr(user, "email", "") or str(user.pk)


class WarehouseRefSerializer(serializers.Serializer):
    id = serializers.CharField(read_only=True)
    name = serializers.CharField(read_only=True, default="")


class InvoiceRefSerializer(serializers.Serializer):
    """A link to another invoice: the one replaced, or a correction of this one."""

    id = serializers.CharField(read_only=True)
    invoice_number = serializers.CharField(read_only=True)
    status = serializers.CharField(read_only=True)


class InvoiceItemSerializer(serializers.Serializer):
    """A frozen invoice line. `line_total` mirrors `InvoiceItem.total`."""

    id = serializers.CharField(read_only=True)
    product_id = serializers.CharField(read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True, default="")
    variant_id = serializers.CharField(read_only=True, allow_null=True)
    description = serializers.CharField(read_only=True, allow_null=True)
    quantity = serializers.IntegerField(read_only=True)
    unit_name = serializers.CharField(read_only=True, allow_null=True)
    unit_multiplier = serializers.DecimalField(max_digits=10, decimal_places=3, read_only=True)
    unit_price = serializers.DecimalField(read_only=True, **MONEY)
    discount_amount = serializers.DecimalField(read_only=True, **MONEY)
    tax_amount = serializers.DecimalField(read_only=True, **MONEY)
    tax_rate = serializers.DecimalField(max_digits=5, decimal_places=2, read_only=True)
    line_total = serializers.SerializerMethodField()

    @extend_schema_field(serializers.DecimalField(**MONEY))
    def get_line_total(self, item: Any) -> str:
        total = item.quantity * item.unit_price - item.discount_amount + item.tax_amount
        return str(total.quantize(CENT))


class InvoiceStateSerializer(serializers.Serializer):
    """
    The identity and lifecycle state of an invoice, with the actions it
    offers. It needs no related rows, so it is the cheap response for command
    endpoints (archive, unarchive) that must not reload the whole invoice.
    """

    id = serializers.CharField(read_only=True)
    invoice_number = serializers.CharField(read_only=True)
    status = serializers.CharField(read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    archived_at = serializers.DateTimeField(read_only=True, allow_null=True)
    is_archived = serializers.SerializerMethodField()
    available_actions = serializers.SerializerMethodField()

    @extend_schema_field(serializers.BooleanField())
    def get_is_archived(self, invoice: Any) -> bool:
        return invoice.archived_at is not None

    @extend_schema_field(serializers.ListField(child=serializers.CharField()))
    def get_available_actions(self, invoice: Any) -> list[str]:
        return available_actions(invoice, self.context.get("permissions", ()))


class InvoiceListSerializer(InvoiceStateSerializer):
    """
    One row of the invoice table: Invoice, Issued, Due, Customer, Total,
    Balance, Status and Actions.
    """

    is_overdue = serializers.SerializerMethodField()
    customer_id = serializers.CharField(read_only=True, allow_null=True)
    customer_name = serializers.CharField(read_only=True)
    customer_company_name = serializers.CharField(read_only=True, allow_null=True)
    issued_at = serializers.DateTimeField(read_only=True, allow_null=True)
    due_date = serializers.DateField(read_only=True)
    currency = serializers.CharField(read_only=True)
    total = serializers.DecimalField(read_only=True, **MONEY)
    amount_paid = serializers.DecimalField(read_only=True, **MONEY)
    balance_due = serializers.DecimalField(read_only=True, **MONEY)
    order_code = serializers.CharField(read_only=True, allow_null=True, default=None)
    is_sale_origin = serializers.BooleanField(read_only=True)
    warehouse = WarehouseRefSerializer(read_only=True)
    sales_rep = UserRefSerializer(read_only=True, allow_null=True)
    created_at = serializers.DateTimeField(read_only=True)

    @extend_schema_field(serializers.BooleanField())
    def get_is_overdue(self, invoice: Any) -> bool:
        """Still carrying a balance past its due date, partially paid included."""
        today = self.context.get("today") or timezone.localdate()
        return invoice.status in OUTSTANDING_STATUSES and invoice.due_date < today


class InvoiceDetailSerializer(InvoiceListSerializer):
    """The detail page and the printable document."""

    customer_tax_id = serializers.CharField(read_only=True, allow_null=True)
    customer_email = serializers.CharField(read_only=True, allow_null=True)
    customer_phone = serializers.CharField(read_only=True, allow_null=True)
    billing_address = serializers.CharField(read_only=True, allow_blank=True)
    shipping_address = serializers.CharField(read_only=True, allow_blank=True)

    subtotal = serializers.DecimalField(read_only=True, **MONEY)
    discount_amount = serializers.DecimalField(read_only=True, **MONEY)
    tax_amount = serializers.DecimalField(read_only=True, **MONEY)

    notes = serializers.CharField(read_only=True, allow_null=True)
    extra_data = serializers.DictField(read_only=True)
    sale_id = serializers.CharField(read_only=True, allow_null=True)
    items = InvoiceItemSerializer(many=True, read_only=True)

    confirmed_by = UserRefSerializer(read_only=True, allow_null=True)
    voided_by = UserRefSerializer(read_only=True, allow_null=True)
    voided_at = serializers.DateTimeField(read_only=True, allow_null=True)
    void_reason = serializers.CharField(read_only=True, allow_null=True)

    replaces = InvoiceRefSerializer(read_only=True, allow_null=True)
    corrections = InvoiceRefSerializer(many=True, read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)


class InvoiceSummarySerializer(serializers.Serializer):
    """
    The cards above the list: Invoiced is `total`, Paid is `paid`, Outstanding
    is `balance_due`, and Overdue is `overdue_amount` with `overdue_count`.
    Drafts and voided invoices are reported apart and never inflate a total.
    """

    count = serializers.IntegerField(read_only=True)
    subtotal = serializers.DecimalField(read_only=True, **MONEY)
    discount = serializers.DecimalField(read_only=True, **MONEY)
    tax = serializers.DecimalField(read_only=True, **MONEY)
    total = serializers.DecimalField(read_only=True, **MONEY)
    paid = serializers.DecimalField(read_only=True, **MONEY)
    balance_due = serializers.DecimalField(read_only=True, **MONEY)
    overdue_count = serializers.IntegerField(read_only=True)
    overdue_amount = serializers.DecimalField(read_only=True, **MONEY)
    draft_count = serializers.IntegerField(read_only=True)
    voided_count = serializers.IntegerField(read_only=True)


class AuditLogSerializer(serializers.Serializer):
    """The Audit tab of the invoice detail page (PRD §31)."""

    id = serializers.CharField(read_only=True)
    action = serializers.CharField(read_only=True)
    actor = UserRefSerializer(read_only=True, required=False, allow_null=True)
    reason = serializers.CharField(read_only=True, required=False, allow_blank=True, allow_null=True)
    approval_id = serializers.CharField(read_only=True, required=False, allow_null=True)
    previous_values = serializers.JSONField(read_only=True, required=False)
    new_values = serializers.JSONField(read_only=True, required=False)
    metadata = serializers.JSONField(read_only=True, required=False)
    ip_address = serializers.CharField(read_only=True, required=False, allow_null=True)
    user_agent = serializers.CharField(read_only=True, required=False, allow_null=True)
    created_at = serializers.DateTimeField(read_only=True)


class AuditCursorField(serializers.CharField):
    """
    An opaque keyset cursor for the audit trail: the `(created_at, pk)` of the
    last row of the previous page, URL-safe. Clients echo `next_cursor` back
    and never build one. Validates to the tuple the service expects.
    """

    default_error_messages = {"invalid_cursor": "Invalid cursor."}

    @staticmethod
    def encode(created_at: datetime, pk: Any) -> str:
        payload = f"{created_at.isoformat()}|{pk}".encode()
        return base64.urlsafe_b64encode(payload).decode().rstrip("=")

    def to_internal_value(self, data: Any) -> tuple[datetime, str]:
        raw = super().to_internal_value(data)
        try:
            padded = raw + "=" * (-len(raw) % 4)
            stamp, pk = base64.urlsafe_b64decode(padded.encode()).decode().split("|", 1)
            return datetime.fromisoformat(stamp), pk
        except (ValueError, UnicodeDecodeError, binascii.Error):
            self.fail("invalid_cursor")


class AuditTrailQuerySerializer(serializers.Serializer):
    """Query parameters of the Audit tab: keyset pagination, newest first."""

    limit = serializers.IntegerField(required=False, min_value=1, max_value=100, default=50)
    cursor = AuditCursorField(required=False)


class AuditTrailPageSerializer(serializers.Serializer):
    """One page of the Audit tab. `next_cursor` is null on the last page."""

    results = AuditLogSerializer(many=True, read_only=True)
    next_cursor = serializers.CharField(read_only=True, allow_null=True)
