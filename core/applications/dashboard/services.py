"""
Dashboard figures (PRD §12). Every section is a handful of grouped, indexed
queries, independent of how many rows a tenant has, and scoped to the
organization, the caller's locations and the optional ?location=.

Money definitions (all amounts exclude tax, which is owed to the government):

- Sales: invoices issued in the period (every sale issues one; standalone
  invoices count too), net of discounts, minus reversals in the period
  (invoices voided, or whose sale was cancelled). Reversals are booked in the
  period they happen, so cancelling next month never rewrites this month.
- Cost of goods sold: what the stock that left on sales cost (frozen on each
  stock movement), less stock restored by returns, cancellations and voids.
- Refunds: sale returns processed in the period (`refund_amount`).
- Expenses: recorded (not voided) operating expenses dated in the period.
- Net profit: Sales - Cost of goods sold - Expenses - Refunds.

Expenses, cost of goods and net profit are "cost and profit" figures (PRD §12):
they are null for callers without VIEW_DASHBOARD_PROFIT.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field
from datetime import date
from datetime import datetime
from datetime import timedelta
from datetime import tzinfo
from decimal import ROUND_HALF_UP
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from django.db.models import Case
from django.db.models import Count
from django.db.models import DecimalField
from django.db.models import ExpressionWrapper
from django.db.models import F
from django.db.models import Q
from django.db.models import Sum
from django.db.models import Value
from django.db.models import When
from django.db.models.functions import Coalesce
from django.db.models.functions import TruncDate
from django.db.models.functions import TruncHour

from core.applications.expenses.models import Expense
from core.applications.inventory.models import Inventory
from core.applications.inventory.models import InventoryLedgerEntry
from core.applications.inventory.models import StockAdjustmentRequest
from core.applications.invoice.models import Invoice
from core.applications.invoice.models import InvoiceItem
from core.applications.invoice.queryset import OUTSTANDING_STATUSES
from core.applications.invoice.queryset import SALE_STATUSES
from core.applications.notification.models import AuditLog
from core.applications.products.models import Product
from core.applications.purchase.models import Purchase
from core.applications.sales.models import Sale
from core.applications.sales.models import SaleReturn
from core.applications.warehouse.access import LocationScope
from core.helper.enums import ApprovalStatusChoices
from core.helper.enums import ExpenseStatus
from core.helper.enums import InvoiceStatusChoices
from core.helper.enums import ProductStatusChoices
from core.helper.enums import PurchaseStatusChoices
from core.helper.enums import SalePaymentStatusChoices
from core.helper.enums import TransactionStatusChoices
from core.helper.enums import TransactionTypeChoices
from core.helper.org_time import day_range

ZERO = Decimal("0.00")
CENT = Decimal("0.01")
MONEY = DecimalField(max_digits=18, decimal_places=2)
EXPIRY_WARNING_DAYS = 30
CACHE_VERSION = "v1"
CURRENCY_SYMBOLS = {"NGN": "₦", "USD": "$", "GBP": "£", "EUR": "€", "GHS": "GH₵", "KES": "KSh"}


# --------------------------------------------------------------------------- #
# Request context
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class DashboardContext:
    organization: Any
    scope: LocationScope
    location: Any  # a Warehouse, or None for "all my locations"
    period: Any  # dashboard.period.Period
    tz: tzinfo
    today: date
    currency: str
    can_view_profit: bool
    can_view_audit: bool
    can_approve_adjustments: bool
    _content_types: dict = field(default_factory=dict, compare=False)

    def scoped(self, queryset, path: str):
        """Caller's locations, then the explicit location filter, on a Warehouse path."""
        queryset = self.scope.apply(queryset, path)
        if self.location is not None:
            queryset = queryset.filter(**{path: self.location.pk})
        return queryset

    def cache_key(self, section: str, extra: str = "") -> str:
        scope_sig = "all" if self.scope.all_locations else ",".join(sorted(str(i) for i in self.scope.location_ids))
        raw = "|".join(
            [
                str(self.organization.pk), section, scope_sig,
                str(self.location.pk if self.location else "*"),
                self.period.key, self.period.start.isoformat(), self.period.end.isoformat(),
                f"p{int(self.can_view_profit)}a{int(self.can_approve_adjustments)}", extra,
            ]
        )
        return f"dashboard:{CACHE_VERSION}:{hashlib.sha256(raw.encode()).hexdigest()}"


def cached(ctx: DashboardContext, section: str, build: Callable[[], dict], extra: str = "") -> dict:
    """Short-lived cache for the heavy aggregate sections (settings.DASHBOARD_CACHE_SECONDS)."""
    ttl = getattr(settings, "DASHBOARD_CACHE_SECONDS", 60)
    if not ttl:
        return build()
    key = ctx.cache_key(section, extra)
    result = cache.get(key)
    if result is None:
        result = build()
        cache.set(key, result, ttl)
    return result


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def _money(value) -> str:
    return str(Decimal(value or 0).quantize(CENT, rounding=ROUND_HALF_UP))


def _change_percent(current: Decimal, previous: Decimal) -> str | None:
    """Percent change vs the previous period, 1 dp. None when there is nothing to compare to."""
    if not previous:
        return None
    change = (Decimal(current) - Decimal(previous)) / abs(Decimal(previous)) * 100
    return str(change.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def _trend(current: Decimal, previous: Decimal) -> str:
    if current > previous:
        return "up"
    if current < previous:
        return "down"
    return "flat"


def _card(current: Decimal, previous: Decimal, **extra) -> dict:
    return {
        "value": _money(current),
        "previous_value": _money(previous),
        "change_percent": _change_percent(current, previous),
        "trend": _trend(current, previous),
        **extra,
    }


def _money_text(amount: Decimal, currency: str) -> str:
    amount = Decimal(amount).quantize(CENT, rounding=ROUND_HALF_UP)
    text = f"{amount:,.0f}" if amount == amount.to_integral() else f"{amount:,.2f}"
    symbol = CURRENCY_SYMBOLS.get(currency.upper())
    return f"{symbol}{text}" if symbol else f"{currency.upper()} {text}"


def _sum(queryset, expression) -> Decimal:
    return queryset.aggregate(total=Coalesce(Sum(expression), Value(ZERO), output_field=MONEY))["total"]


def _ct(ctx: DashboardContext, model) -> int:
    if model not in ctx._content_types:
        ctx._content_types[model] = ContentType.objects.get_for_model(model).pk
    return ctx._content_types[model]


_NET_OF_TAX = ExpressionWrapper(F("total") - F("tax_amount"), output_field=MONEY)
_MOVED_COST = ExpressionWrapper(F("quantity_moved") * F("unit_cost"), output_field=MONEY)


# --------------------------------------------------------------------------- #
# Money figures
# --------------------------------------------------------------------------- #
def _invoices(ctx: DashboardContext):
    return ctx.scoped(Invoice.objects.filter(organization=ctx.organization), "warehouse")


def _issued(ctx, start: date, end: date):
    lo, hi = day_range(start, end, ctx.tz)
    return _invoices(ctx).filter(issued_at__gte=lo, issued_at__lt=hi)


def _reversals(ctx, start: date, end: date) -> tuple[Any, Any]:
    """(voided in the period, sale cancelled in the period); disjoint, both already issued."""
    lo, hi = day_range(start, end, ctx.tz)
    issued = _invoices(ctx).filter(issued_at__isnull=False)
    voided = issued.filter(status=InvoiceStatusChoices.CANCELLED, voided_at__gte=lo, voided_at__lt=hi)
    sale_cancelled = issued.exclude(status=InvoiceStatusChoices.CANCELLED).filter(
        sale__cancelled_at__gte=lo, sale__cancelled_at__lt=hi
    )
    return voided, sale_cancelled


def sales_total(ctx, start: date, end: date) -> Decimal:
    voided, cancelled = _reversals(ctx, start, end)
    return _sum(_issued(ctx, start, end), _NET_OF_TAX) - _sum(voided, _NET_OF_TAX) - _sum(cancelled, _NET_OF_TAX)


def sales_count(ctx, start: date, end: date) -> int:
    """Sales issued in the period that are still live (not voided or cancelled)."""
    return (
        _issued(ctx, start, end)
        .filter(status__in=SALE_STATUSES)
        .filter(Q(sale__isnull=True) | Q(sale__cancelled_at__isnull=True))
        .count()
    )


def cost_of_goods_sold(ctx, start: date, end: date) -> Decimal:
    lo, hi = day_range(start, end, ctx.tz)
    movements = ctx.scoped(
        InventoryLedgerEntry.objects.filter(
            transaction__organization=ctx.organization,
            transaction__created_at__gte=lo,
            transaction__created_at__lt=hi,
            transaction__transaction_type__in=(TransactionTypeChoices.SALE, TransactionTypeChoices.RETURN),
            transaction__content_type_id__in=(_ct(ctx, Sale), _ct(ctx, SaleReturn), _ct(ctx, Invoice)),
        ),
        "warehouse",
    )
    # Stock out is negative, restocks positive: cost = -(sum of quantity x unit cost).
    return -_sum(movements, _MOVED_COST)


def _sale_returns(ctx):
    return ctx.scoped(
        SaleReturn.objects.filter(organization=ctx.organization).exclude(
            status__in=(TransactionStatusChoices.FAILED, TransactionStatusChoices.REVERSED)
        ),
        "original_sale__warehouse",
    )


def refunds_total(ctx, start: date, end: date) -> Decimal:
    lo, hi = day_range(start, end, ctx.tz)
    return _sum(_sale_returns(ctx).filter(created_at__gte=lo, created_at__lt=hi), F("refund_amount"))


def _expenses(ctx):
    return ctx.scoped(Expense.objects.for_organization(ctx.organization), "location")


def expenses_total(ctx, start: date, end: date) -> Decimal:
    return _sum(_expenses(ctx).recorded().between(start, end), F("amount"))


def _figures(ctx, start: date, end: date) -> dict[str, Decimal]:
    figures = {"sales": sales_total(ctx, start, end), "refunds": refunds_total(ctx, start, end)}
    if ctx.can_view_profit:
        figures["expenses"] = expenses_total(ctx, start, end)
        figures["cogs"] = cost_of_goods_sold(ctx, start, end)
        figures["net_profit"] = figures["sales"] - figures["cogs"] - figures["expenses"] - figures["refunds"]
    return figures


# --------------------------------------------------------------------------- #
# Sections
# --------------------------------------------------------------------------- #
def overview(ctx: DashboardContext) -> dict:
    def build():
        period = ctx.period
        now = _figures(ctx, period.start, period.end)
        before = _figures(ctx, period.previous_start, period.previous_end)
        cards = {
            "sales": _card(now["sales"], before["sales"], count=sales_count(ctx, period.start, period.end)),
            "refunds": _card(now["refunds"], before["refunds"]),
            "expenses": None,
            "net_profit": None,
        }
        if ctx.can_view_profit:
            cards["expenses"] = _card(now["expenses"], before["expenses"])
            cards["net_profit"] = _card(
                now["net_profit"],
                before["net_profit"],
                cost_of_goods_sold=_money(now["cogs"]),
            )
        return {"cards": cards}

    return cached(ctx, "overview", build)


def _granularity(period) -> str:
    if period.days == 1:
        return "hour"
    if period.days <= 14:
        return "day"
    if period.days <= 92:
        return "week"
    return "month"


def _sales_by_slot(ctx, start: date, end: date, granularity: str) -> dict[Any, Decimal]:
    """Net sales per day (or per hour for one-day periods), keyed by date / aware datetime."""
    trunc = TruncHour if granularity == "hour" else TruncDate
    lo, hi = day_range(start, end, ctx.tz)
    totals: dict[Any, Decimal] = {}

    def add(queryset, date_field: str, sign: int):
        rows = (
            queryset.annotate(slot=trunc(date_field, tzinfo=ctx.tz))
            .values("slot")
            .annotate(total=Coalesce(Sum(_NET_OF_TAX), Value(ZERO), output_field=MONEY))
        )
        for row in rows:
            slot = row["slot"]
            if isinstance(slot, datetime) and granularity != "hour":
                slot = slot.date()
            totals[slot] = totals.get(slot, ZERO) + sign * row["total"]

    add(_invoices(ctx).filter(issued_at__gte=lo, issued_at__lt=hi), "issued_at", 1)
    voided, cancelled = _reversals(ctx, start, end)
    add(voided, "voided_at", -1)
    add(cancelled, "sale__cancelled_at", -1)
    return totals


def _buckets(period, granularity: str, tz) -> list[tuple[str, date | datetime, date | datetime]]:
    """(label, first slot, last slot) for every bucket, including empty ones."""
    start, end = period.start, period.end
    if granularity == "hour":
        midnight = day_range(start, start, tz)[0]
        return [(f"{h:02d}:00", midnight + timedelta(hours=h), midnight + timedelta(hours=h)) for h in range(24)]
    if granularity == "day":
        days = [start + timedelta(days=i) for i in range(period.days)]
        return [(d.strftime("%a %d"), d, d) for d in days]
    if granularity == "week":
        buckets, cursor, index = [], start, 1
        while cursor <= end:
            last = min(cursor + timedelta(days=6), end)
            buckets.append((f"W{index}", cursor, last))
            cursor, index = last + timedelta(days=1), index + 1
        return buckets
    buckets, cursor = [], start
    while cursor <= end:
        next_month = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
        last = min(next_month - timedelta(days=1), end)
        buckets.append((cursor.strftime("%b %Y"), cursor, last))
        cursor = next_month
    return buckets


def sales_chart(ctx: DashboardContext) -> dict:
    def build():
        period = ctx.period
        granularity = _granularity(period)
        slots = _sales_by_slot(ctx, period.start, period.end, granularity)
        points = []
        for label, first, last in _buckets(period, granularity, ctx.tz):
            if granularity == "hour":
                value = sum((v for k, v in slots.items() if k.astimezone(ctx.tz).hour == first.hour), ZERO)
                span = {"start": first.isoformat(), "end": (first + timedelta(minutes=59)).isoformat()}
            else:
                value = sum((v for k, v in slots.items() if first <= k <= last), ZERO)
                span = {"start": first.isoformat(), "end": last.isoformat()}
            points.append({"label": label, **span, "value": _money(value)})

        total = sales_total(ctx, period.start, period.end)
        previous = sales_total(ctx, period.previous_start, period.previous_end)
        return {
            "granularity": granularity,
            "total": _money(total),
            "previous_total": _money(previous),
            "change_percent": _change_percent(total, previous),
            "trend": _trend(total, previous),
            "max_value": _money(max((Decimal(p["value"]) for p in points), default=ZERO)),
            "points": points,
        }

    return cached(ctx, "sales_chart", build)


def alerts(ctx: DashboardContext) -> dict:
    """Current-state alerts (not period-based), most urgent first. Only non-zero alerts are returned."""

    def build():
        items: list[dict] = []

        stock = (
            Product.objects.filter(
                organization=ctx.organization, status=ProductStatusChoices.ACTIVE, track_inventory=True
            )
            .with_stock(ctx.scope, ctx.location.pk if ctx.location else None)
            .aggregate(
                out=Count("pk", filter=Q(stock_on_hand__lte=0)),
                low=Count("pk", filter=Q(stock_on_hand__gt=0, stock_on_hand__lte=F("minimum_stock_level"))),
            )
        )
        if stock["out"]:
            items.append(_alert(
                "out_of_stock", "urgent", "Out of Stock",
                f"{stock['out']} product{'s are' if stock['out'] != 1 else ' is'} out of stock",
                stock["out"], {"resource": "products", "query": {"low_stock": "true"}},
            ))
        if stock["low"]:
            items.append(_alert(
                "low_stock", "urgent", "Low Stock Alert",
                f"{stock['low']} product{'s are' if stock['low'] != 1 else ' is'} below their reorder level",
                stock["low"], {"resource": "products", "query": {"low_stock": "true"}},
            ))

        overdue = (
            _invoices(ctx)
            .filter(status__in=OUTSTANDING_STATUSES, due_date__lt=ctx.today)
            .annotate(balance=ExpressionWrapper(F("total") - F("amount_paid"), output_field=MONEY))
            .filter(balance__gt=0)
        )
        summary = overdue.aggregate(count=Count("pk"), amount=Coalesce(Sum("balance"), Value(ZERO), output_field=MONEY))
        if summary["count"]:
            top = overdue.select_related("customer").order_by("-balance", "due_date").first()
            customer = getattr(top.customer, "name", "") or "a customer"
            if summary["count"] == 1:
                message = f"{_money_text(top.balance, ctx.currency)} from {customer} is overdue"
            else:
                message = (
                    f"{_money_text(summary['amount'], ctx.currency)} across {summary['count']} invoices is overdue; "
                    f"largest is {_money_text(top.balance, ctx.currency)} from {customer}"
                )
            items.append(_alert(
                "overdue_payment", "urgent", "Overdue Payment", message, summary["count"],
                {"resource": "invoices", "query": {"overdue": "true"}},
                amount=_money(summary["amount"]),
                top={"invoice_id": str(top.pk), "invoice_number": top.invoice_number,
                     "customer_name": customer, "balance": _money(top.balance), "due_date": top.due_date.isoformat()},
            ))

        batches = ctx.scoped(
            Inventory.objects.filter(product__organization=ctx.organization, quantity__gt=0, batch__isnull=False),
            "warehouse",
        )
        expired = batches.filter(batch__expiry_date__lt=ctx.today).values("product").distinct().count()
        expiring = (
            batches.filter(
                batch__expiry_date__gte=ctx.today,
                batch__expiry_date__lte=ctx.today + timedelta(days=EXPIRY_WARNING_DAYS),
            ).values("product").distinct().count()
        )
        if expired:
            items.append(_alert(
                "expired_stock", "urgent", "Expired Stock",
                f"{expired} product{'s have' if expired != 1 else ' has'} expired stock on hand",
                expired, None,
            ))
        if expiring:
            items.append(_alert(
                "expiry_warning", "warning", "Expiry Warning",
                f"{expiring} product{'s are' if expiring != 1 else ' is'} expiring within {EXPIRY_WARNING_DAYS} days",
                expiring, None,
            ))

        if ctx.can_approve_adjustments:
            pending = ctx.scoped(
                StockAdjustmentRequest.objects.filter(
                    warehouse__organization=ctx.organization, status=ApprovalStatusChoices.PENDING
                ),
                "warehouse",
            ).count()
            if pending:
                items.append(_alert(
                    "pending_approvals", "warning", "Pending Approvals",
                    f"{pending} stock adjustment{'s are' if pending != 1 else ' is'} waiting for approval",
                    pending, {"resource": "inventory/adjustments", "query": {}},
                ))

        order = {"urgent": 0, "warning": 1, "info": 2}
        items.sort(key=lambda item: order[item["severity"]])
        return {"items": items, "count": len(items)}

    return cached(ctx, "alerts", build, extra=ctx.today.isoformat())


def _alert(kind, severity, title, message, count, link, **extra) -> dict:
    return {"type": kind, "severity": severity, "title": title, "message": message, "count": count,
            "link": link, **extra}


def top_products(ctx: DashboardContext, limit: int) -> dict:
    def build():
        lo, hi = day_range(ctx.period.start, ctx.period.end, ctx.tz)
        lines = ctx.scoped(
            InvoiceItem.objects.filter(
                invoice__organization=ctx.organization,
                invoice__status__in=SALE_STATUSES,
                invoice__issued_at__gte=lo,
                invoice__issued_at__lt=hi,
            ).filter(Q(invoice__sale__isnull=True) | Q(invoice__sale__cancelled_at__isnull=True)),
            "invoice__warehouse",
        )
        units = ExpressionWrapper(F("quantity") * F("unit_multiplier"), output_field=DecimalField(max_digits=18, decimal_places=3))
        revenue = ExpressionWrapper(
            F("quantity") * F("unit_price") - F("discount_amount")
            - Case(When(invoice__prices_include_tax=True, then=F("tax_amount")), default=Value(ZERO)),
            output_field=MONEY,
        )
        rows = list(
            lines.values("product", "product__name", "product__sku")
            .annotate(units_sold=Sum(units), revenue=Sum(revenue))
            .order_by("-units_sold", "-revenue", "product__name")[:limit]
        )
        top_units = rows[0]["units_sold"] if rows else 0
        items = [
            {
                "rank": index,
                "product": {"id": str(row["product"]), "name": row["product__name"], "sku": row["product__sku"]},
                "units_sold": _units(row["units_sold"]),
                "revenue": _money(row["revenue"]),
                "share_of_top_percent": (
                    str((Decimal(row["units_sold"]) / Decimal(top_units) * 100).quantize(Decimal("0.1")))
                    if top_units else "0.0"
                ),
            }
            for index, row in enumerate(rows, start=1)
        ]
        return {"items": items, "max_units_sold": _units(top_units)}

    return cached(ctx, "top_products", build, extra=str(limit))


def _units(value) -> int | str:
    value = Decimal(value or 0)
    return int(value) if value == value.to_integral() else str(value.normalize())


# --------------------------------------------------------------------------- #
# Recent activity (audit log)
# --------------------------------------------------------------------------- #
_ACTIVITY_VERBS = {
    "sales_order.created": "recorded", "sales_order.fulfilled": "completed",
    "sales_order.cancelled": "cancelled", "sales_return.created": "processed a return for",
    "invoice.created": "created", "invoice.confirmed": "confirmed", "invoice.voided": "voided",
    "invoice.archived": "archived", "invoice.unarchived": "restored",
    "product.created": "added product", "product.updated": "updated product",
    "product.archived": "archived product", "product.restored": "restored product",
    "inventory.adjusted": "adjusted stock of", "inventory.adjustment_rejected": "rejected a stock adjustment for",
    "inventory.opening_stock_set": "added opening stock for",
    "purchase_order.created": "created", "purchase_order.approved": "approved",
    "purchase_order.updated": "updated", "purchase_order.received": "received goods for",
    "purchase_order.cancelled": "cancelled", "purchase_order.deleted": "deleted",
    "purchase_order.payment_recorded": "recorded a supplier payment for",
    "purchase_return.created": "returned goods to the supplier for",
    "purchase_invoice.recorded": "recorded a supplier invoice for", "purchase_invoice.voided": "voided supplier invoice",
    "customer.created": "added customer", "customer.updated": "updated customer",
    "customer.deactivated": "deactivated customer", "customer.deleted": "deleted customer",
    "customer.credit_adjusted": "changed the credit limit of",
    "expense.recorded": "recorded expense", "expense.updated": "updated expense", "expense.voided": "voided expense",
    "location.created": "added location", "location.default_changed": "set the default location to",
    "invitation.created": "invited", "invitation.accepted": "accepted an invitation to",
    "membership.role_changed": "changed the role of", "membership.locations_changed": "changed the locations of",
    "organization.updated": "updated the business settings",
}


def _actor_name(log: AuditLog) -> str:
    actor = log.actor
    if actor is not None and getattr(actor, "name", ""):
        return actor.name
    if log.actor_email:
        return log.actor_email.split("@")[0]
    return "System"


def _activity_message(log: AuditLog, currency: str) -> str:
    actor = _actor_name(log)
    verb = _ACTIVITY_VERBS.get(log.action, log.action.replace(".", " ").replace("_", " "))
    subject = log.resource_repr
    new = log.new_values or {}
    meta = log.metadata or {}
    if log.action.startswith("expense.") and new.get("title"):
        subject = new["title"] + (f" ({_money_text(Decimal(new['amount']), currency)})" if new.get("amount") else "")
    elif log.action == "inventory.adjusted" and "quantity_change" in meta:
        change = int(meta["quantity_change"])
        return f"{actor} adjusted stock of {subject} by {change:+d}"
    elif "quantity" in meta:
        subject = f"{meta['quantity']} units of {subject}" if log.action == "inventory.opening_stock_set" else subject
    if log.action == "organization.updated":
        return f"{actor} {verb}"
    return f"{actor} {verb} {subject}".strip()


def recent_activity(ctx: DashboardContext, limit: int) -> dict:
    """
    Needs VIEW_AUDIT_LOGS and access to every location: audit events carry no
    location, so a branch-limited member would otherwise see other branches.
    """
    if not (ctx.can_view_audit and ctx.scope.all_locations):
        return {"available": False, "items": []}
    logs = (
        AuditLog.objects.filter(organization=ctx.organization)
        .select_related("actor")
        .order_by("-created_at")[:limit]
    )
    return {
        "available": True,
        "items": [
            {
                "id": str(log.pk),
                "action": log.action,
                "message": _activity_message(log, ctx.currency),
                "actor": {"id": log.actor_id, "name": _actor_name(log)},
                "resource": {"type": log.resource_type, "id": log.resource_id, "label": log.resource_repr},
                "occurred_at": log.created_at.isoformat(),
            }
            for log in logs
        ],
    }


# --------------------------------------------------------------------------- #
# Recent transactions (sales, refunds, purchases, expenses)
# --------------------------------------------------------------------------- #
def _branch(warehouse) -> dict | None:
    return {"id": str(warehouse.pk), "name": warehouse.name} if warehouse is not None else None


def _sale_status(sale: Sale) -> str:
    if sale.cancelled_at:
        return "cancelled"
    return {
        SalePaymentStatusChoices.PAID: "successful",
        SalePaymentStatusChoices.REFUNDED: "refunded",
    }.get(sale.payment_status, "pending")


_RETURN_STATUS = {
    TransactionStatusChoices.COMPLETED: "successful",
    TransactionStatusChoices.PENDING: "pending",
    TransactionStatusChoices.FAILED: "failed",
    TransactionStatusChoices.REVERSED: "cancelled",
}
_PURCHASE_STATUS = {
    PurchaseStatusChoices.RECEIVED: "successful",
    PurchaseStatusChoices.CANCELLED: "cancelled",
}
STATUS_LABELS = {
    "successful": "Successful", "pending": "Pending", "failed": "Failed",
    "cancelled": "Cancelled", "refunded": "Refunded",
}
TYPE_LABELS = {"sale": "Sale", "refund": "Refund", "purchase": "Purchase", "expense": "Operational Expenses"}


def _row(kind, obj_id, reference, amount, status, branch, occurred_at) -> dict:
    return {
        "id": str(obj_id), "type": kind, "type_display": TYPE_LABELS[kind], "reference": reference,
        "amount": _money(amount), "status": status, "status_display": STATUS_LABELS[status],
        "branch": branch, "occurred_at": occurred_at.isoformat(),
    }


def recent_transactions(ctx: DashboardContext, limit: int) -> dict:
    """
    The newest `limit` money events in the period across sales, refunds,
    purchases and (with the profit permission) expenses: `limit` rows per type
    at most, merged in Python. `total` counts them all in the period.
    """
    lo, hi = day_range(ctx.period.start, ctx.period.end, ctx.tz)
    in_period = {"created_at__gte": lo, "created_at__lt": hi}

    sales = ctx.scoped(Sale.objects.filter(organization=ctx.organization, **in_period), "warehouse")
    returns = _sale_returns(ctx).filter(**in_period)
    purchases = ctx.scoped(
        Purchase.objects.filter(organization=ctx.organization, **in_period).exclude(status=PurchaseStatusChoices.DRAFT),
        "warehouse",
    )
    expenses = _expenses(ctx).filter(**in_period) if ctx.can_view_profit else Expense.objects.none()

    rows = [
        _row("sale", s.pk, s.sale_id, s.total, _sale_status(s), _branch(s.warehouse), s.created_at)
        for s in sales.select_related("warehouse").order_by("-created_at")[:limit]
    ]
    rows += [
        _row("refund", r.pk, f"RET-{str(r.pk).replace('-', '')[:8].upper()}", r.refund_amount,
             _RETURN_STATUS.get(r.status, "pending"), _branch(r.original_sale.warehouse), r.created_at)
        for r in returns.select_related("original_sale__warehouse").order_by("-created_at")[:limit]
    ]
    rows += [
        _row("purchase", p.pk, p.purchase_number, p.ordered_value, _PURCHASE_STATUS.get(p.status, "pending"),
             _branch(p.warehouse), p.created_at)
        for p in purchases.select_related("warehouse").prefetch_related("items").order_by("-created_at")[:limit]
    ]
    rows += [
        _row("expense", e.pk, e.expense_number, e.amount,
             "cancelled" if e.status == ExpenseStatus.VOID else "successful", _branch(e.location), e.created_at)
        for e in expenses.select_related("location").order_by("-created_at")[:limit]
    ]
    rows.sort(key=lambda row: row["occurred_at"], reverse=True)
    total = sales.count() + returns.count() + purchases.count() + expenses.count()
    return {"items": rows[:limit], "total": total}
