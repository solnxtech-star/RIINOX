from __future__ import annotations

from datetime import date
from datetime import datetime
from datetime import time
from datetime import timedelta
from typing import TYPE_CHECKING
from typing import Any
from typing import Final
from typing import Literal

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
from django.utils import timezone

from core.helper.enums import InvoiceStatusChoices

if TYPE_CHECKING:
    from datetime import tzinfo

    from core.applications.users.models import Organization

__all__ = [
    "ARCHIVABLE_STATUSES",
    "DEFAULT_ORDERING",
    "DEFAULT_PAGE_SIZE",
    "MAX_PAGE_SIZE",
    "ORDERING_FIELDS",
    "OUTSTANDING_STATUSES",
    "PIPELINE_STATUSES",
    "SALE_STATUSES",
    "ArchiveScope",
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
BASE_UNITS: Final = DecimalField(max_digits=18, decimal_places=3)
DEFAULT_PAGE_SIZE: Final = 50
MAX_PAGE_SIZE: Final = 200

_S = InvoiceStatusChoices
# Confirmed and beyond: these count as real sales.
SALE_STATUSES: Final = (_S.CONFIRMED, _S.PARTIALLY_PAID, _S.PAID, _S.OVERDUE)
# Confirmed but still carrying a balance.
OUTSTANDING_STATUSES: Final = (_S.CONFIRMED, _S.PARTIALLY_PAID, _S.OVERDUE)
# Not yet issued: they hold no stock and no receivable.
PIPELINE_STATUSES: Final = (_S.DRAFT, _S.PENDING)
# Settled for good: only these may be archived, so an invoice that still carries
# a balance can never be hidden from the default list.
ARCHIVABLE_STATUSES: Final = (_S.PAID, _S.CANCELLED)

# Which invoices a list shows: the default hides archived ones.
ArchiveScope = Literal["active", "archived", "all"]

# Columns a list may be sorted by (PRD §47). Anything else is rejected, so an
# arbitrary client-supplied string can never reach `order_by`.
_ORDERING_EXPRESSIONS: Final = {
    "created_at": F("created_at"),
    "issued_at": F("issued_at"),
    "due_date": F("due_date"),
    "total": F("total"),
    "invoice_number": F("invoice_number"),
    "balance_due": ExpressionWrapper(F("total") - F("amount_paid"), output_field=MONEY),
}
ORDERING_FIELDS: Final = frozenset(_ORDERING_EXPRESSIONS)
DEFAULT_ORDERING: Final = "-created_at"


def _require_organization(organization: Organization | None) -> Organization:
    if organization is None:
        msg = "An organization is required to scope this query."
        raise ValueError(msg)
    return organization


def _clamp_page_size(limit: int) -> int:
    return max(1, min(limit, MAX_PAGE_SIZE))


def _day_start(day: date, tz: tzinfo | None) -> datetime:
    """Midnight at the start of `day` in `tz` (the project timezone if None)."""
    return datetime.combine(day, time.min, tzinfo=tz or timezone.get_current_timezone())


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

    def for_customer(self, customer):
        return self.filter(customer=customer)

    def by_sales_rep(self, user):
        return self.filter(sales_rep=user)

    # -- origin ------------------------------------------------------------- #

    def for_sale(self, sale_id):
        """Every invoice, live or voided, ever issued for one sale."""
        return self.filter(sale_id=sale_id)

    def sale_origin(self):
        """Invoices issued from a sale. The sale owns their stock movement."""
        return self.filter(sale__isnull=False)

    def standalone(self):
        """Invoices created without a sale."""
        return self.filter(sale__isnull=True)

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
        """
        Past due and not settled. `today` is passed in so timezones stay
        explicit: callers pass the organization's own date (PRD §7).
        """
        return self.outstanding().filter(due_date__lt=today)

    def corrections(self):
        """Invoices that replace a voided original (PRD §24 correction flow)."""
        return self.filter(replaces__isnull=False)

    # -- archiving ---------------------------------------------------------- #

    def active(self):
        """Invoices that are not archived: what a list shows by default."""
        return self.filter(archived_at__isnull=True)

    def archived(self):
        """Invoices that were archived. They still count in reports and totals."""
        return self.filter(archived_at__isnull=False)

    def with_archive_scope(self, scope: ArchiveScope = "active"):
        """
        The list filter behind `archived=false|true|all`: "active" hides archived
        invoices, "archived" shows only them, "all" shows both.
        """
        if scope == "active":
            return self.active()
        if scope == "archived":
            return self.archived()
        if scope == "all":
            return self
        msg = f"Unknown archive scope {scope!r}. Use 'active', 'archived' or 'all'."
        raise ValueError(msg)

    # -- filtering ---------------------------------------------------------- #

    def issued_between(
        self,
        start: date | None = None,
        end: date | None = None,
        *,
        tz: tzinfo | None = None,
    ):
        """
        Filter on the issue date, inclusive on both days, where a day is a
        calendar day in `tz` (pass the organization's timezone). Drafts and
        Pending invoices have no issue date, so they drop out.

        Built as a half-open datetime range on `issued_at` rather than a
        `__date` cast, so the (organization, issued_at) index stays usable.
        """
        qs = self
        if start is not None:
            qs = qs.filter(issued_at__gte=_day_start(start, tz))
        if end is not None:
            qs = qs.filter(issued_at__lt=_day_start(end + timedelta(days=1), tz))
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
        Prefix match on number and customer email (index-friendly) plus customer name.
        For very large tenants, back customer-name search with a trigram index.
        """
        term = (term or "").strip()
        if not term:
            return self
        return self.filter(
            Q(invoice_number__istartswith=term)
            | Q(customer__name__icontains=term)
            | Q(customer__email__istartswith=term)
        )

    # -- ordering ----------------------------------------------------------- #

    def ordered_by(self, ordering: str | None = None):
        """
        Sort by one of `ORDERING_FIELDS`, written `field` (ascending) or `-field`
        (descending), newest first when omitted. Rows with no value (an issue
        date on a draft) always sort last, and the primary key breaks ties so
        page-number pagination never repeats or skips a row between pages.

        Raises ValueError for any other field: validate user input first (the
        API's ordering parameter lists the allowed values).
        """
        key = (ordering or DEFAULT_ORDERING).strip()
        descending = key.startswith("-")
        field = key.removeprefix("-")
        expression = _ORDERING_EXPRESSIONS.get(field)
        if expression is None:
            msg = f"Cannot order invoices by {field!r}. Allowed: {sorted(ORDERING_FIELDS)}."
            raise ValueError(msg)
        primary = expression.desc(nulls_last=True) if descending else expression.asc(nulls_last=True)
        return self.order_by(primary, "-pk")

    # -- loading strategies (avoid N+1) ------------------------------------- #

    def for_list(self):
        """
        List pages: one query, no items, no N+1 on the columns the table shows.
        `order_code` is the readable code of the sale behind a sale-origin
        invoice (the "Order" column), fetched as one joined column rather than
        a whole Sale row. It is NULL for standalone invoices.
        """
        return self.select_related(
            "customer",
            "sales_rep",
            "confirmed_by",
            "warehouse",
        ).annotate(order_code=F("sale__sale_id"))

    def for_detail(self):
        """
        Detail/PDF pages: parties, the voiding user and the replaced invoice in
        the main query; line items (with their products and variants, in a
        stable order) and any corrected replacements in two more. Three queries
        however many lines.
        """
        items = Prefetch(
            "items",
            queryset=apps.get_model("invoice", "InvoiceItem")
            .objects.select_related("product", "variant")
            .order_by("pk"),
        )
        return (
            self.for_list()
            .select_related("replaces", "voided_by")
            .prefetch_related(items, "corrections")
        )

    def with_computed_subtotal(self):
        """
        Live sum of line amounts before discount and tax, computed in the
        database (for Drafts, PRD §24). Prefer this over
        `Invoice.computed_subtotal` when listing many invoices.
        """
        return self.annotate(
            live_subtotal=Coalesce(
                Sum(ExpressionWrapper(F("items__quantity") * F("items__unit_price"), output_field=MONEY)),
                0,
                output_field=MONEY,
            )
        )

    def with_balance_due(self):
        """
        Adds `balance_due_amount` (total minus amount paid) computed in the
        database. It is named differently from the `Invoice.balance_due`
        property on purpose: Django sets annotations as attributes, and a
        read-only property of the same name would raise AttributeError.
        """
        return self.annotate(
            balance_due_amount=ExpressionWrapper(F("total") - F("amount_paid"), output_field=MONEY)
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

    def totals(self, *, today: date | None = None) -> dict[str, Any]:
        """
        Count and money totals for whatever this queryset currently holds.

        Pass `today` (the organization's own date) to also get `overdue_count`
        and `overdue_amount`: invoices still carrying a balance past their due
        date, including partially paid ones. They come from the same single
        query.
        """
        balance = ExpressionWrapper(F("total") - F("amount_paid"), output_field=MONEY)
        aggregates: dict[str, Any] = {
            "count": Count("pk"),
            "subtotal": Coalesce(Sum("subtotal"), 0, output_field=MONEY),
            "discount": Coalesce(Sum("discount_amount"), 0, output_field=MONEY),
            "tax": Coalesce(Sum("tax_amount"), 0, output_field=MONEY),
            "total": Coalesce(Sum("total"), 0, output_field=MONEY),
            "paid": Coalesce(Sum("amount_paid"), 0, output_field=MONEY),
            "balance_due": Coalesce(Sum(balance), 0, output_field=MONEY),
        }
        if today is not None:
            past_due = Q(status__in=OUTSTANDING_STATUSES, due_date__lt=today)
            aggregates["overdue_count"] = Count("pk", filter=past_due)
            aggregates["overdue_amount"] = Coalesce(
                Sum(balance, filter=past_due),
                0,
                output_field=MONEY,
            )
        return self.aggregate(**aggregates)

    def pipeline_counts(self) -> dict[str, int]:
        """
        How many invoices are not sales yet (Draft and Pending) and how many are
        voided, in one query. Reported beside `totals()` so they are visible
        without ever counting toward revenue.
        """
        return self.aggregate(
            draft_count=Count("pk", filter=Q(status__in=PIPELINE_STATUSES)),
            voided_count=Count("pk", filter=Q(status=_S.CANCELLED)),
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
        return self.select_related("product", "variant")

    def with_line_total(self):
        """
        Adds `line_total` computed in the database, matching `InvoiceItem.total`:
        quantity x unit_price, minus the line discount, plus the line tax.
        """
        return self.annotate(
            line_total=ExpressionWrapper(
                F("quantity") * F("unit_price") - F("discount_amount") + F("tax_amount"),
                output_field=MONEY,
            )
        )

    def top_selling_products(self, organization: Organization | None, limit: int = 10):
        """
        Dashboard metric 'top-selling products' (PRD §12): units and revenue
        per product in one grouped query.

        The organization is a required argument and the scoping happens here, so
        this metric can never be computed across tenants by forgetting a filter.
        `units_sold` is in base units (quantity x unit multiplier), so cartons
        and pieces of the same product add up correctly. Revenue is net of line
        discounts and excludes tax.
        """
        return (
            self.for_organization(organization)
            .on_confirmed_sales()
            .values("product")
            .annotate(
                units_sold=Coalesce(
                    Sum(
                        ExpressionWrapper(
                            F("quantity") * F("unit_multiplier"),
                            output_field=BASE_UNITS,
                        )
                    ),
                    0,
                    output_field=BASE_UNITS,
                ),
                revenue=Coalesce(
                    Sum(
                        ExpressionWrapper(
                            F("quantity") * F("unit_price") - F("discount_amount"),
                            output_field=MONEY,
                        )
                    ),
                    0,
                    output_field=MONEY,
                ),
            )
            .order_by("-units_sold", "-revenue")[: _clamp_page_size(limit)]
        )


InvoiceItemManager = auto_prefetch.Manager.from_queryset(InvoiceItemQuerySet)
