from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any
from typing import Final

import auto_prefetch
from django.apps import apps
from django.db.models import Count
from django.db.models import DecimalField
from django.db.models import ExpressionWrapper
from django.db.models import F
from django.db.models import Prefetch
from django.db.models import Q
from django.db.models import Sum
from django.db.models.functions import Coalesce

from core.helper.enums import InvoiceStatusChoices

if TYPE_CHECKING:
    from datetime import date
    from datetime import datetime

    from core.applications.users.models import Organization

__all__ = [
    "OUTSTANDING_STATUSES",
    "SALE_STATUSES",
    "ClientManager",
    "ClientQuerySet",
    "DocumentSequenceManager",
    "DocumentSequenceQuerySet",
    "DocumentTemplateManager",
    "DocumentTemplateQuerySet",
    "InvoiceItemManager",
    "InvoiceItemQuerySet",
    "InvoiceManager",
    "InvoiceQuerySet",
]

MONEY: Final = DecimalField(max_digits=14, decimal_places=2)
DEFAULT_PAGE_SIZE: Final = 50
MAX_PAGE_SIZE: Final = 200

_S = InvoiceStatusChoices
# Confirmed and beyond: these count as real sales.
SALE_STATUSES: Final = (_S.CONFIRMED, _S.PARTIALLY_PAID, _S.PAID, _S.OVERDUE)
# Confirmed but still carrying a balance.
OUTSTANDING_STATUSES: Final = (_S.CONFIRMED, _S.PARTIALLY_PAID, _S.OVERDUE)


def _require_organization(organization: Organization | None) -> Organization:
    if organization is None:
        msg = "An organization is required to scope this query."
        raise ValueError(msg)
    return organization


def _clamp_page_size(limit: int) -> int:
    return max(1, min(limit, MAX_PAGE_SIZE))


class DocumentTemplateQuerySet(auto_prefetch.QuerySet):
    """Templates are either shared (organization is NULL) or owned by one tenant."""

    def active(self):
        return self.filter(is_active=True)

    def shared(self):
        """Default/System templates (no owning organization)."""
        return self.filter(organization__isnull=True)

    def custom(self):
        """Organization-owned (Custom) templates, across all tenants."""
        return self.filter(organization__isnull=False)

    def owned_by(self, organization: Organization | None):
        """Only the given organization's own Custom templates."""
        return self.filter(organization=_require_organization(organization))

    def defaults(self):
        """THE shared default per template type."""
        return self.shared().filter(is_default=True)

    def of_type(self, template_type: str):
        return self.filter(template_type=template_type)

    def available_to(self, organization: Organization | None):
        """Shared templates plus the organization's own. Never another tenant's."""
        scope = Q(organization__isnull=True)
        if organization is not None:
            scope |= Q(organization=organization)
        return self.filter(scope)


DocumentTemplateManager = auto_prefetch.Manager.from_queryset(DocumentTemplateQuerySet)


# --------------------------------------------------------------------------- #
# DocumentSequence
# --------------------------------------------------------------------------- #


class DocumentSequenceQuerySet(auto_prefetch.QuerySet):
    def for_organization(self, organization: Organization | None):
        return self.filter(organization=_require_organization(organization))

    def of_type(self, document_type: str):
        return self.filter(document_type=document_type)

    def locked(self):
        """
        Row-lock for the numbering critical section (SELECT ... FOR UPDATE).
        Only valid inside a transaction; Django raises otherwise, which is the
        safety net that keeps numbering gapless and race-free.
        """
        return self.select_for_update()


DocumentSequenceManager = auto_prefetch.Manager.from_queryset(DocumentSequenceQuerySet)


# --------------------------------------------------------------------------- #
# Invoice
# --------------------------------------------------------------------------- #


class InvoiceQuerySet(auto_prefetch.QuerySet):
    # -- tenant scoping ----------------------------------------------------- #

    def for_organization(self, organization: Organization | None):
        return self.filter(organization=_require_organization(organization))

    def for_client(self, client):
        return self.filter(client=client)

    def by_sales_rep(self, user):
        return self.filter(sales_rep=user)

    # -- lifecycle groupings ------------------------------------------------ #

    def with_status(self, *statuses: str):
        return self.filter(status__in=statuses)

    def drafts(self):
        return self.filter(status=_S.DRAFT)

    def not_voided(self):
        """
        Excludes voided/cancelled records.
        Completed invoices are never deleted (PRD §44).
        """
        return self.exclude(status=_S.CANCELLED)

    def voided(self):
        return self.filter(status=_S.CANCELLED)

    def confirmed_sales(self):
        """Invoices that count as real sales: confirmed and beyond, not voided."""
        return self.filter(status__in=SALE_STATUSES)

    def outstanding(self):
        """Confirmed invoices that still carry a balance."""
        return self.filter(status__in=OUTSTANDING_STATUSES)

    def overdue(self, today: date):
        """Past due and not settled. `today` is passed in so timezones stay explicit."""
        return self.outstanding().filter(due_date__lt=today)

    def corrections(self):
        """Invoices that replace a voided original (PRD §24 correction flow)."""
        return self.filter(replaces__isnull=False)

    # -- filtering ---------------------------------------------------------- #

    def issued_between(self, start: date | None = None, end: date | None = None):
        qs = self
        if start is not None:
            qs = qs.filter(created_at__date__gte=start)
        if end is not None:
            qs = qs.filter(created_at__date__lte=end)
        return qs

    def due_between(self, start: date | None = None, end: date | None = None):
        qs = self
        if start is not None:
            qs = qs.filter(due_date__gte=start)
        if end is not None:
            qs = qs.filter(due_date__lte=end)
        return qs

    def search(self, term: str):
        """
        Prefix match on number and client email (index-friendly) plus client name.
        For very large tenants, back client-name search with a trigram index.
        """
        term = (term or "").strip()
        if not term:
            return self
        return self.filter(
            Q(invoice_number__istartswith=term)
            | Q(client__name__icontains=term)
            | Q(client__email__istartswith=term)
        )

    # -- loading strategies (avoid N+1) ------------------------------------- #

    def for_list(self):
        """List pages: one query, no items, no N+1 on the columns the table shows."""
        return self.select_related("client", "sales_rep", "confirmed_by", "warehouse")

    def for_detail(self):
        """
        Detail/PDF pages: parties, the voiding user and the replaced invoice in
        the main query; line items (with their products, in a stable order) and
        any corrected replacements in two more. Three queries however many lines.
        """
        items = Prefetch(
            "items",
            queryset=apps.get_model("invoice", "InvoiceItem")
            .objects.select_related("product")
            .order_by("pk"),
        )
        return (
            self.for_list()
            .select_related("replaces", "voided_by")
            .prefetch_related(items, "corrections")
        )

    def with_computed_subtotal(self):
        """
        Live sum of line items computed in the database (for Drafts, PRD §24).
        Prefer this over `Invoice.computed_subtotal` when listing many invoices.
        """
        return self.annotate(
            live_subtotal=Coalesce(
                Sum(ExpressionWrapper(F("items__quantity") * F("items__unit_price"), output_field=MONEY)),
                0,
                output_field=MONEY,
            )
        )

    # -- pagination --------------------------------------------------------- #

    def page_after(self, cursor: tuple[datetime, Any] | None = None, limit: int = DEFAULT_PAGE_SIZE):
        """
        Keyset page, newest first. `cursor` is the (created_at, pk) of the last
        row of the previous page, or None for the first page. Cost is constant
        regardless of depth, unlike OFFSET. Pair with the (organization,
        -created_at) index.
        """
        qs = self.order_by("-created_at", "-pk")
        if cursor is not None:
            created_at, pk = cursor
            qs = qs.filter(Q(created_at__lt=created_at) | Q(created_at=created_at, pk__lt=pk))
        return qs[: _clamp_page_size(limit)]

    # -- aggregates --------------------------------------------------------- #

    def totals(self) -> dict[str, Any]:
        """Count and money totals for whatever this queryset currently holds."""
        return self.aggregate(
            count=Count("pk"),
            subtotal=Coalesce(Sum("subtotal"), 0, output_field=MONEY),
            discount=Coalesce(Sum("discount_amount"), 0, output_field=MONEY),
            tax=Coalesce(Sum("tax_amount"), 0, output_field=MONEY),
            total=Coalesce(Sum("total"), 0, output_field=MONEY),
        )

    def sales_by_rep(self):
        """Dashboard metric 'sales by staff' (PRD §12): one grouped query."""
        return (
            self.confirmed_sales()
            .values("sales_rep", "sales_rep__email")
            .annotate(invoice_count=Count("pk"), revenue=Coalesce(Sum("total"), 0, output_field=MONEY))
            .order_by("-revenue")
        )


InvoiceManager = auto_prefetch.Manager.from_queryset(InvoiceQuerySet)


# --------------------------------------------------------------------------- #
# InvoiceItem
# --------------------------------------------------------------------------- #


class InvoiceItemQuerySet(auto_prefetch.QuerySet):
    """
    Line items carry no organization column; they resolve to a tenant through
    their invoice (PRD §6). Always scope with `for_organization`.
    """

    def for_organization(self, organization: Organization | None):
        return self.filter(invoice__organization=_require_organization(organization))

    def for_invoice(self, invoice):
        return self.filter(invoice=invoice)

    def for_product(self, product):
        return self.filter(product=product)

    def on_confirmed_sales(self):
        """Only lines from invoices that count as real sales."""
        return self.filter(invoice__status__in=SALE_STATUSES)

    def with_product(self):
        return self.select_related("product")

    def with_line_total(self):
        """Adds `line_total` (quantity x unit_price) computed in the database."""
        return self.annotate(line_total=ExpressionWrapper(F("quantity") * F("unit_price"), output_field=MONEY))

    def top_selling_products(self, limit: int = 10):
        """
        Dashboard metric 'top-selling products' (PRD §12): units and revenue
        per product in one grouped query. Scope with `for_organization` first.
        """
        return (
            self.on_confirmed_sales()
            .values("product")
            .annotate(
                units_sold=Coalesce(Sum("quantity"), 0),
                revenue=Coalesce(
                    Sum(ExpressionWrapper(F("quantity") * F("unit_price"), output_field=MONEY)),
                    0,
                    output_field=MONEY,
                ),
            )
            .order_by("-units_sold")[: _clamp_page_size(limit)]
        )


InvoiceItemManager = auto_prefetch.Manager.from_queryset(InvoiceItemQuerySet)


# --------------------------------------------------------------------------- #
# Client
# --------------------------------------------------------------------------- #


class ClientQuerySet(auto_prefetch.QuerySet):
    def for_organization(self, organization: Organization | None):
        return self.filter(organization=_require_organization(organization))

    def active(self):
        return self.filter(is_active=True)

    def with_balance(self):
        """Clients who currently owe money (system-maintained cache, PRD §20)."""
        return self.filter(current_balance__gt=0)

    def over_credit_limit(self):
        """Balance exceeds a positive credit limit. A limit of 0 means no credit was granted."""
        return self.filter(credit_limit__gt=0, current_balance__gt=F("credit_limit"))

    def search(self, term: str):
        term = (term or "").strip()
        if not term:
            return self
        return self.filter(Q(name__icontains=term) | Q(email__istartswith=term) | Q(phone__startswith=term))

    def page_after(self, cursor: tuple[str, Any] | None = None, limit: int = DEFAULT_PAGE_SIZE):
        """Keyset page ordered by (name, pk). `cursor` is the last row's (name, pk)."""
        qs = self.order_by("name", "pk")
        if cursor is not None:
            name, pk = cursor
            qs = qs.filter(Q(name__gt=name) | Q(name=name, pk__gt=pk))
        return qs[: _clamp_page_size(limit)]


ClientManager = auto_prefetch.Manager.from_queryset(ClientQuerySet)
