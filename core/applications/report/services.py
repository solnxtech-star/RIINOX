"""
Report figures and analytics (PRD §27, §34, §35).

Reuses period resolution, timezone helpers, location scoping, and core money
definitions from `dashboard.services` and `warehouse.access`.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date
from datetime import datetime
from datetime import timedelta
from decimal import ROUND_HALF_UP
from decimal import Decimal
from typing import Any

from django.contrib.contenttypes.models import ContentType
from django.db.models import Case
from django.db.models import Count
from django.db.models import DecimalField
from django.db.models import ExpressionWrapper
from django.db.models import F
from django.db.models import Max
from django.db.models import Q
from django.db.models import Sum
from django.db.models import Value
from django.db.models import When
from django.db.models.functions import Coalesce
from django.db.models.functions import TruncDate

from core.applications.customers.models import Customer
from core.applications.dashboard.period import Period
from core.applications.dashboard.period import _shift_months
from core.applications.dashboard.services import CENT
from core.applications.dashboard.services import EXPIRY_WARNING_DAYS
from core.applications.dashboard.services import MONEY
from core.applications.dashboard.services import ZERO
from core.applications.dashboard.services import DashboardContext
from core.applications.dashboard.services import _buckets
from core.applications.dashboard.services import _issued
from core.applications.dashboard.services import _money
from core.applications.dashboard.services import _money_text
from core.applications.dashboard.services import _sale_returns
from core.applications.dashboard.services import _sales_by_slot
from core.applications.dashboard.services import _units
from core.applications.dashboard.services import cost_of_goods_sold
from core.applications.dashboard.services import sales_count
from core.applications.dashboard.services import sales_total
from core.applications.inventory.models import Inventory
from core.applications.inventory.models import InventoryLedgerEntry
from core.applications.invoice.models import Invoice
from core.applications.invoice.models import InvoiceItem
from core.applications.invoice.models import PurchaseInvoice
from core.applications.invoice.queryset import OUTSTANDING_STATUSES
from core.applications.invoice.queryset import SALE_STATUSES
from core.applications.payment.models import InvoicePayment
from core.applications.products.models import Product
from core.applications.purchase.models import Purchase
from core.applications.purchase.models import PurchaseItem
from core.applications.sales.models import Sale
from core.applications.sales.models import SaleReturn
from core.helper.enums import PaymentMethod
from core.helper.enums import PaymentStatus
from core.helper.enums import ProductStatusChoices
from core.helper.enums import PurchasePaymentStatusChoices
from core.helper.enums import PurchaseStatusChoices
from core.helper.enums import TransactionTypeChoices
from core.helper.org_time import day_range

DEFAULT_PAGE_SIZE = 5
MAX_PAGE_SIZE = 100
SLOW_MOVING_DAYS = 30
MONTHLY_LOOKBACK_MONTHS = 5

_NET_OF_TAX = ExpressionWrapper(F("total") - F("tax_amount"), output_field=MONEY)
_INVOICE_LINE_REVENUE = ExpressionWrapper(
    F("quantity") * F("unit_price")
    - F("discount_amount")
    - Case(When(invoice__prices_include_tax=True, then=F("tax_amount")), default=Value(ZERO)),
    output_field=MONEY,
)
_INVOICE_LINE_UNITS = ExpressionWrapper(
    F("quantity") * F("unit_multiplier"),
    output_field=DecimalField(max_digits=18, decimal_places=3),
)
_MOVED_COST = ExpressionWrapper(F("quantity_moved") * F("unit_cost"), output_field=MONEY)

_PAYMENT_METHOD_LABELS = {
    PaymentMethod.BANK_TRANSFER: "Bank transfer",
    PaymentMethod.CASH: "Cash",
    PaymentMethod.CARD: "Card",
    PaymentMethod.POS: "POS terminal",
    PaymentMethod.ONLINE: "Online payment",
    PaymentMethod.STRIPE: "Stripe",
    PaymentMethod.PAYSTACK: "Paystack",
    PaymentMethod.NOWPAYMENTS: "NowPayments",
    PaymentMethod.WALLET: "Wallet",
}


@dataclass(frozen=True)
class ReportContext(DashboardContext):
    """Carries organization, location scope, period, and report permissions."""

    can_view_sales_reports: bool = False
    can_view_inventory_reports: bool = False
    can_view_financial_reports: bool = False


def _percentage(part: Decimal, total: Decimal) -> str:
    if not total:
        return "0.0"
    ratio = (Decimal(part) / Decimal(total) * Decimal("100")).quantize(
        Decimal("0.1"), rounding=ROUND_HALF_UP
    )
    return str(ratio)


def _date_span_label(start: date, end: date) -> str:
    if start.year == end.year and start.month == end.month:
        return f"{start.day} to {end.day} {end.strftime('%b')}"
    return f"{start.strftime('%d %b')} to {end.strftime('%d %b')}"


def _live_issued_invoices(ctx: ReportContext, start: date, end: date):
    return (
        _issued(ctx, start, end)
        .filter(status__in=SALE_STATUSES)
        .filter(Q(sale__isnull=True) | Q(sale__cancelled_at__isnull=True))
    )


def _live_invoice_items(ctx: ReportContext, start: date, end: date):
    lower_bound, upper_bound = day_range(start, end, ctx.tz)
    return ctx.scoped(
        InvoiceItem.objects.filter(
            invoice__organization=ctx.organization,
            invoice__status__in=SALE_STATUSES,
            invoice__issued_at__gte=lower_bound,
            invoice__issued_at__lt=upper_bound,
        ).filter(Q(invoice__sale__isnull=True) | Q(invoice__sale__cancelled_at__isnull=True)),
        "invoice__warehouse",
    )


def _daily_or_bucket_points(
    ctx: ReportContext,
    period: Period,
    granularity: str,
    slots: dict[Any, Decimal],
) -> list[dict[str, Any]]:
    points = []
    for label, first_day, last_day in _buckets(period, granularity, ctx.tz):
        bucket_value = sum(
            (amount for slot_date, amount in slots.items() if first_day <= slot_date <= last_day),
            ZERO,
        )
        points.append(
            {
                "label": label,
                "start": first_day.isoformat(),
                "end": last_day.isoformat(),
                "value": _money(bucket_value),
            }
        )
    return points


def _paginate_list(items: list[dict[str, Any]], page: int, page_size: int) -> dict[str, Any]:
    total_count = len(items)
    offset = (page - 1) * page_size
    sliced = items[offset : offset + page_size]
    return {
        "total_count": total_count,
        "page": page,
        "page_size": page_size,
        "items": sliced,
    }


# --------------------------------------------------------------------------- #
# Overview Report
# --------------------------------------------------------------------------- #
def _gross_profit_over_time(ctx: ReportContext, granularity: str) -> dict[str, Any] | None:
    if not (ctx.can_view_financial_reports or ctx.can_view_profit):
        return None

    period = ctx.period
    period_gross_profit = sales_total(ctx, period.start, period.end) - cost_of_goods_sold(
        ctx, period.start, period.end
    )

    if granularity == "monthly":
        window_start = _shift_months(ctx.today.replace(day=1), -(MONTHLY_LOOKBACK_MONTHS - 1))
        if period.start < window_start:
            window_start = period.start.replace(day=1)
        chart_period = Period(
            key="monthly_window",
            label="Monthly",
            start=window_start,
            end=ctx.today,
            previous_start=window_start,
            previous_end=ctx.today,
        )
        bucket_list = _buckets(chart_period, "month", ctx.tz)
    else:
        chart_granularity = "day" if granularity == "daily" else "week"
        bucket_list = _buckets(period, chart_granularity, ctx.tz)

    points = []
    for label, first_day, last_day in bucket_list:
        bucket_profit = sales_total(ctx, first_day, last_day) - cost_of_goods_sold(
            ctx, first_day, last_day
        )
        is_partial = False
        if granularity == "monthly":
            month_last_day = date(
                last_day.year,
                last_day.month,
                calendar.monthrange(last_day.year, last_day.month)[1],
            )
            is_partial = last_day < month_last_day
        elif granularity == "weekly":
            is_partial = (last_day - first_day).days < 6 and last_day == ctx.today

        points.append(
            {
                "label": label,
                "start": first_day.isoformat(),
                "end": last_day.isoformat(),
                "value": _money(bucket_profit),
                "is_partial": is_partial,
            }
        )

    peak_point = max(points, key=lambda point: Decimal(point["value"]), default=None)
    insight = None
    if peak_point and Decimal(peak_point["value"]) > ZERO:
        insight = (
            f"Gross profit was highest in {peak_point['label']}, "
            f"at {_money_text(Decimal(peak_point['value']), ctx.currency)}."
        )

    return {
        "granularity": granularity,
        "total": _money(period_gross_profit),
        "date_range_label": _date_span_label(period.start, period.end),
        "points": points,
        "insight": insight,
    }


def _sales_by_category(ctx: ReportContext) -> dict[str, Any]:
    lines = _live_invoice_items(ctx, ctx.period.start, ctx.period.end)
    rows = list(
        lines.values("product__category_id", "product__category__name")
        .annotate(revenue=Coalesce(Sum(_INVOICE_LINE_REVENUE), Value(ZERO), output_field=MONEY))
        .order_by("-revenue")
    )
    total_revenue = sum((row["revenue"] for row in rows), ZERO)

    top_rows = rows[:4]
    other_revenue = sum((row["revenue"] for row in rows[4:]), ZERO)

    items = []
    for row in top_rows:
        category_name = row["product__category__name"] or "Uncategorized"
        items.append(
            {
                "category_id": str(row["product__category_id"]) if row["product__category_id"] else None,
                "name": category_name,
                "amount": _money(row["revenue"]),
                "percentage": _percentage(row["revenue"], total_revenue),
            }
        )
    if other_revenue > ZERO:
        items.append(
            {
                "category_id": None,
                "name": "Other",
                "amount": _money(other_revenue),
                "percentage": _percentage(other_revenue, total_revenue),
            }
        )

    insight = None
    if items and total_revenue > ZERO:
        top_item = items[0]
        insight = f"{top_item['name']} made up {top_item['percentage']}% of {ctx.period.label.lower()} sales."

    return {
        "total": _money(total_revenue),
        "items": items,
        "insight": insight,
    }


def _payment_status_and_methods(ctx: ReportContext) -> dict[str, Any]:
    lower_bound, upper_bound = day_range(ctx.period.start, ctx.period.end, ctx.tz)
    payments = ctx.scoped(
        InvoicePayment.objects.filter(
            invoice__organization=ctx.organization,
            is_voided=False,
            payment__status=PaymentStatus.SUCCESS,
            received_on__gte=lower_bound,
            received_on__lt=upper_bound,
        ),
        "invoice__warehouse",
    )
    method_rows = list(
        payments.values("payment__method")
        .annotate(total=Coalesce(Sum("amount"), Value(ZERO), output_field=MONEY))
        .order_by("-total")
    )

    outstanding_invoices = _live_issued_invoices(ctx, ctx.period.start, ctx.period.end).filter(
        status__in=OUTSTANDING_STATUSES
    )
    unpaid_amount = outstanding_invoices.aggregate(
        total=Coalesce(
            Sum(
                Case(
                    When(total__gt=F("amount_paid"), then=F("total") - F("amount_paid")),
                    default=Value(ZERO),
                    output_field=MONEY,
                )
            ),
            Value(ZERO),
            output_field=MONEY,
        )
    )["total"]

    paid_total = sum((row["total"] for row in method_rows), ZERO)
    combined_total = paid_total + unpaid_amount

    segments = []
    for row in method_rows:
        method_key = row["payment__method"]
        segments.append(
            {
                "key": method_key,
                "label": _PAYMENT_METHOD_LABELS.get(method_key, method_key.replace("_", " ").title()),
                "amount": _money(row["total"]),
                "percentage": _percentage(row["total"], combined_total),
            }
        )
    if unpaid_amount > ZERO:
        segments.append(
            {
                "key": "not_yet_paid",
                "label": "Not yet paid",
                "amount": _money(unpaid_amount),
                "percentage": _percentage(unpaid_amount, combined_total),
            }
        )

    insight = None
    paid_segments = [segment for segment in segments if segment["key"] != "not_yet_paid"]
    if paid_segments:
        top_method = paid_segments[0]
        insight = f"{top_method['label']} was the most used way to pay, at {top_method['percentage']}%."

    return {
        "total": _money(combined_total),
        "period_label": ctx.period.label,
        "segments": segments,
        "insight": insight,
    }


def _browse_reports(ctx: ReportContext) -> list[dict[str, Any]]:
    catalog = [
        (
            "sales",
            "Sales",
            "Sales summary, best sellers and sales by customer",
            ctx.can_view_sales_reports,
        ),
        (
            "inventory",
            "Inventory",
            "Stock value, what is running low and what is expiring",
            ctx.can_view_inventory_reports,
        ),
        (
            "suppliers",
            "Suppliers",
            "What you owe suppliers and what you buy from them",
            ctx.can_view_inventory_reports,
        ),
        (
            "refunds",
            "Refunds",
            "Money paid back to customers",
            ctx.can_view_sales_reports,
        ),
        (
            "payments",
            "Payments",
            "Money received and how people paid",
            ctx.can_view_financial_reports,
        ),
        (
            "customers",
            "Customers",
            "New customers, who owes you and sales by customer type",
            ctx.can_view_sales_reports,
        ),
    ]
    return [
        {"key": key, "title": title, "description": description}
        for key, title, description, allowed in catalog
        if allowed
    ]


def overview_report(ctx: ReportContext, granularity: str = "monthly") -> dict[str, Any]:
    """Overview screen: gross profit trend, sales by category, payment methods, and report links."""
    return {
        "gross_profit_over_time": _gross_profit_over_time(ctx, granularity),
        "sales_by_category": _sales_by_category(ctx),
        "payment_status_and_methods": _payment_status_and_methods(ctx),
        "browse_reports": _browse_reports(ctx),
    }


# --------------------------------------------------------------------------- #
# Sales Report
# --------------------------------------------------------------------------- #
def sales_report(ctx: ReportContext, granularity: str = "daily") -> dict[str, Any]:
    """Sales report: sales over time, top products by sales, top customers by sales."""
    period = ctx.period
    chart_granularity = "day" if granularity == "daily" else "week"
    slots = _sales_by_slot(ctx, period.start, period.end, chart_granularity)
    points = _daily_or_bucket_points(ctx, period, chart_granularity, slots)

    total_sales = sales_total(ctx, period.start, period.end)
    order_count = sales_count(ctx, period.start, period.end)
    avg_per_day = (total_sales / Decimal(period.days)).quantize(CENT, rounding=ROUND_HALF_UP)

    lines = _live_invoice_items(ctx, period.start, period.end)
    product_rows = list(
        lines.values("product_id", "product__name", "product__sku")
        .annotate(
            revenue=Coalesce(Sum(_INVOICE_LINE_REVENUE), Value(ZERO), output_field=MONEY),
            units_sold=Coalesce(Sum(_INVOICE_LINE_UNITS), Value(Decimal("0.000"))),
        )
        .order_by("-revenue", "product__name")[:5]
    )
    top_products = [
        {
            "product_id": str(row["product_id"]),
            "name": row["product__name"],
            "sku": row["product__sku"],
            "revenue": _money(row["revenue"]),
            "units_sold": _units(row["units_sold"]),
            "share_percent": _percentage(row["revenue"], total_sales),
        }
        for row in product_rows
    ]
    top_product_insight = None
    if top_products and total_sales > ZERO:
        top_product_insight = f"{top_products[0]['name']} is {top_products[0]['share_percent']}% of sales"

    customer_rows = list(
        _live_issued_invoices(ctx, period.start, period.end)
        .filter(customer__isnull=False)
        .values("customer_id", "customer__name")
        .annotate(
            orders_count=Count("pk"),
            total_sales=Coalesce(Sum(_NET_OF_TAX), Value(ZERO), output_field=MONEY),
        )
        .order_by("-total_sales", "customer__name")[:5]
    )
    top_customers = [
        {
            "customer_id": str(row["customer_id"]),
            "name": row["customer__name"],
            "orders_count": row["orders_count"],
            "total_sales": _money(row["total_sales"]),
            "share_percent": _percentage(row["total_sales"], total_sales),
        }
        for row in customer_rows
    ]
    top_customer_insight = None
    if top_customers and total_sales > ZERO:
        top_customer_insight = (
            f"{top_customers[0]['name']} is your biggest customer, "
            f"at {top_customers[0]['share_percent']}% of sales."
        )

    return {
        "sales_over_time": {
            "granularity": granularity,
            "total": _money(total_sales),
            "order_count": order_count,
            "average_per_day": _money(avg_per_day),
            "points": points,
            "insight": f"Sales averaged about {_money_text(avg_per_day, ctx.currency)} a day.",
        },
        "top_products_by_sales": {
            "items": top_products,
            "insight": top_product_insight,
        },
        "top_customers_by_sales": {
            "items": top_customers,
            "insight": top_customer_insight,
        },
    }


# --------------------------------------------------------------------------- #
# Customer Report
# --------------------------------------------------------------------------- #
def customer_report(ctx: ReportContext) -> dict[str, Any]:
    """Customer report: new customers over time, outstanding balances, sales by customer type."""
    period = ctx.period
    lower_bound, upper_bound = day_range(period.start, period.end, ctx.tz)

    new_customers_qs = Customer.objects.filter(
        organization=ctx.organization,
        created_at__gte=lower_bound,
        created_at__lt=upper_bound,
    )
    daily_counts = {
        row["slot"].date() if isinstance(row["slot"], datetime) else row["slot"]: Decimal(row["count"])
        for row in new_customers_qs.annotate(slot=TruncDate("created_at", tzinfo=ctx.tz))
        .values("slot")
        .annotate(count=Count("pk"))
    }
    chart_granularity = "day" if period.days <= 31 else "week"
    customer_points = []
    for label, first_day, last_day in _buckets(period, chart_granularity, ctx.tz):
        bucket_count = int(
            sum(
                (count for slot_date, count in daily_counts.items() if first_day <= slot_date <= last_day),
                ZERO,
            )
        )
        customer_points.append(
            {
                "label": label,
                "start": first_day.isoformat(),
                "end": last_day.isoformat(),
                "value": bucket_count,
            }
        )
    total_new_customers = new_customers_qs.count()
    peak_customer_point = max(customer_points, key=lambda point: point["value"], default=None)
    new_customer_insight = None
    if peak_customer_point and peak_customer_point["value"] > 0:
        new_customer_insight = f"Most new customers joined on {peak_customer_point['label']}."

    outstanding_invoices = ctx.scoped(
        Invoice.objects.filter(
            organization=ctx.organization,
            status__in=OUTSTANDING_STATUSES,
            customer__isnull=False,
        ).filter(total__gt=F("amount_paid")),
        "warehouse",
    )
    balance_rows = list(
        outstanding_invoices.values("customer_id", "customer__name")
        .annotate(
            outstanding=Coalesce(Sum(F("total") - F("amount_paid")), Value(ZERO), output_field=MONEY)
        )
        .order_by("-outstanding", "customer__name")
    )
    total_outstanding = sum((row["outstanding"] for row in balance_rows), ZERO)
    customer_ids = [row["customer_id"] for row in balance_rows[:5]]
    lifetime_sales_map = {
        row["customer_id"]: row["total_sales"]
        for row in ctx.scoped(
            Invoice.objects.filter(
                organization=ctx.organization,
                customer_id__in=customer_ids,
                status__in=SALE_STATUSES,
            ).filter(Q(sale__isnull=True) | Q(sale__cancelled_at__isnull=True)),
            "warehouse",
        )
        .values("customer_id")
        .annotate(total_sales=Coalesce(Sum(_NET_OF_TAX), Value(ZERO), output_field=MONEY))
    }
    outstanding_items = [
        {
            "customer_id": str(row["customer_id"]),
            "name": row["customer__name"],
            "total_sales": _money(lifetime_sales_map.get(row["customer_id"], ZERO)),
            "outstanding": _money(row["outstanding"]),
        }
        for row in balance_rows[:5]
    ]
    outstanding_insight = None
    if outstanding_items:
        top_debtor = outstanding_items[0]
        outstanding_insight = (
            f"{top_debtor['name']} owes the most, "
            f"at {_money_text(Decimal(top_debtor['outstanding']), ctx.currency)}."
        )

    period_invoices = _live_issued_invoices(ctx, period.start, period.end)
    split_totals = period_invoices.aggregate(
        new_sales=Coalesce(
            Sum(
                Case(
                    When(
                        Q(customer__isnull=True) | Q(customer__created_at__gte=lower_bound),
                        then=_NET_OF_TAX,
                    ),
                    default=Value(ZERO),
                    output_field=MONEY,
                )
            ),
            Value(ZERO),
            output_field=MONEY,
        ),
        returning_sales=Coalesce(
            Sum(
                Case(
                    When(customer__created_at__lt=lower_bound, then=_NET_OF_TAX),
                    default=Value(ZERO),
                    output_field=MONEY,
                )
            ),
            Value(ZERO),
            output_field=MONEY,
        ),
    )
    new_sales = split_totals["new_sales"]
    returning_sales = split_totals["returning_sales"]
    combined_sales = new_sales + returning_sales
    customer_type_segments = [
        {
            "key": "returning_customers",
            "label": "Returning customers",
            "amount": _money(returning_sales),
            "percentage": _percentage(returning_sales, combined_sales),
        },
        {
            "key": "new_customers",
            "label": "New customers",
            "amount": _money(new_sales),
            "percentage": _percentage(new_sales, combined_sales),
        },
    ]
    dominant_segment = max(customer_type_segments, key=lambda segment: Decimal(segment["amount"]))
    customer_type_insight = None
    if combined_sales > ZERO:
        customer_type_insight = (
            f"{dominant_segment['label']} brought in "
            f"{dominant_segment['percentage']}% of {period.label.lower()} sales."
        )

    return {
        "new_customers_over_time": {
            "total": total_new_customers,
            "points": customer_points,
            "insight": new_customer_insight,
        },
        "outstanding_customer_balances": {
            "total_outstanding": _money(total_outstanding),
            "customer_count": len(balance_rows),
            "items": outstanding_items,
            "insight": outstanding_insight,
        },
        "sales_by_customer_type": {
            "total": _money(combined_sales),
            "segments": customer_type_segments,
            "insight": customer_type_insight,
        },
    }


# --------------------------------------------------------------------------- #
# Supplier Report
# --------------------------------------------------------------------------- #
def _purchase_totals_queryset(ctx: ReportContext, start: date | None = None, end: date | None = None):
    purchases = ctx.scoped(
        Purchase.objects.filter(organization=ctx.organization).exclude(
            status__in=(PurchaseStatusChoices.DRAFT, PurchaseStatusChoices.CANCELLED)
        ),
        "warehouse",
    )
    if start is not None and end is not None:
        purchases = purchases.filter(order_date__gte=start, order_date__lte=end)
    return purchases.select_related("vendor").prefetch_related("items", "purchase_invoices")


def supplier_report(ctx: ReportContext) -> dict[str, Any]:
    """Supplier report: purchases over time, spending by supplier, outstanding supplier balances."""
    period = ctx.period
    period_purchases = list(_purchase_totals_queryset(ctx, period.start, period.end))

    daily_spend: dict[date, Decimal] = {}
    vendor_spend: dict[str, dict[str, Any]] = {}
    total_purchases = ZERO

    for purchase in period_purchases:
        amount = Decimal(str(purchase.ordered_value)).quantize(CENT, rounding=ROUND_HALF_UP)
        total_purchases += amount
        daily_spend[purchase.order_date] = daily_spend.get(purchase.order_date, ZERO) + amount

        vendor_id = str(purchase.vendor_id)
        if vendor_id not in vendor_spend:
            vendor_spend[vendor_id] = {
                "vendor_id": vendor_id,
                "name": purchase.vendor.company_name,
                "amount": ZERO,
            }
        vendor_spend[vendor_id]["amount"] += amount

    chart_granularity = "day" if period.days <= 31 else "week"
    purchase_points = _daily_or_bucket_points(ctx, period, chart_granularity, daily_spend)
    peak_purchase_point = max(
        purchase_points, key=lambda point: Decimal(point["value"]), default=None
    )
    purchases_insight = None
    if peak_purchase_point and Decimal(peak_purchase_point["value"]) > ZERO:
        purchases_insight = (
            f"Most was bought on {peak_purchase_point['label']}, "
            f"at {_money_text(Decimal(peak_purchase_point['value']), ctx.currency)}."
        )

    sorted_spending = sorted(
        vendor_spend.values(), key=lambda entry: (-entry["amount"], entry["name"])
    )
    spending_items = [
        {
            "vendor_id": entry["vendor_id"],
            "name": entry["name"],
            "amount": _money(entry["amount"]),
        }
        for entry in sorted_spending[:5]
    ]
    spending_insight = None
    if spending_items:
        top_supplier = spending_items[0]
        spending_insight = (
            f"{top_supplier['name']} supplied the most, "
            f"at {_money_text(Decimal(top_supplier['amount']), ctx.currency)}."
        )

    unpaid_purchases = list(
        _purchase_totals_queryset(ctx).exclude(payment_status=PurchasePaymentStatusChoices.PAID)
    )
    vendor_balances: dict[str, dict[str, Any]] = {}
    total_outstanding = ZERO
    for purchase in unpaid_purchases:
        due = purchase.balance_due
        if due <= ZERO:
            continue
        total_outstanding += due
        vendor_id = str(purchase.vendor_id)
        if vendor_id not in vendor_balances:
            vendor_balances[vendor_id] = {
                "vendor_id": vendor_id,
                "name": purchase.vendor.company_name,
                "amount": ZERO,
            }
        vendor_balances[vendor_id]["amount"] += due

    sorted_balances = sorted(
        vendor_balances.values(), key=lambda entry: (-entry["amount"], entry["name"])
    )
    balance_items = [
        {
            "vendor_id": entry["vendor_id"],
            "name": entry["name"],
            "amount": _money(entry["amount"]),
        }
        for entry in sorted_balances[:5]
    ]
    balance_insight = None
    if balance_items:
        top_owed = balance_items[0]
        balance_insight = (
            f"You owe {top_owed['name']} the most, "
            f"at {_money_text(Decimal(top_owed['amount']), ctx.currency)}."
        )

    return {
        "purchases_over_time": {
            "total": _money(total_purchases),
            "points": purchase_points,
            "insight": purchases_insight,
        },
        "spending_by_supplier": {
            "supplier_count": len(sorted_spending),
            "items": spending_items,
            "insight": spending_insight,
        },
        "outstanding_supplier_balances": {
            "total_outstanding": _money(total_outstanding),
            "items": balance_items,
            "insight": balance_insight,
        },
    }


# --------------------------------------------------------------------------- #
# Refund Report
# --------------------------------------------------------------------------- #
def refund_report(
    ctx: ReportContext,
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
    include_all_rows: bool = False,
) -> dict[str, Any]:
    """Refund report: refunds over time and itemized refund list."""
    period = ctx.period
    lower_bound, upper_bound = day_range(period.start, period.end, ctx.tz)
    returns_qs = (
        _sale_returns(ctx)
        .filter(created_at__gte=lower_bound, created_at__lt=upper_bound)
        .select_related("original_sale")
        .prefetch_related("original_sale__invoices")
        .order_by("-refund_amount", "-created_at")
    )

    daily_refunds = {
        row["slot"].date() if isinstance(row["slot"], datetime) else row["slot"]: row["total"]
        for row in returns_qs.annotate(slot=TruncDate("created_at", tzinfo=ctx.tz))
        .values("slot")
        .annotate(total=Coalesce(Sum("refund_amount"), Value(ZERO), output_field=MONEY))
    }
    chart_granularity = "day" if period.days <= 31 else "week"
    refund_points = _daily_or_bucket_points(ctx, period, chart_granularity, daily_refunds)

    total_refunds = sum(daily_refunds.values(), ZERO)
    peak_point = max(refund_points, key=lambda point: Decimal(point["value"]), default=None)
    over_time_insight = None
    if peak_point and Decimal(peak_point["value"]) > ZERO:
        over_time_insight = (
            f"Most was refunded on {peak_point['label']}, "
            f"at {_money_text(Decimal(peak_point['value']), ctx.currency)}."
        )

    rows = []
    for sale_return in returns_qs:
        first_invoice = next(iter(sale_return.original_sale.invoices.all()), None)
        reference = f"RET-{str(sale_return.pk).replace('-', '')[:8].upper()}"
        rows.append(
            {
                "id": str(sale_return.pk),
                "ref": reference,
                "invoice_number": first_invoice.invoice_number if first_invoice else sale_return.original_sale.sale_id,
                "reason": sale_return.return_reason,
                "amount": _money(sale_return.refund_amount),
                "date": sale_return.created_at.astimezone(ctx.tz).date().isoformat(),
            }
        )

    largest_insight = None
    if rows:
        largest = rows[0]
        largest_insight = (
            f"{largest['ref']} was the largest refund, "
            f"at {_money_text(Decimal(largest['amount']), ctx.currency)}."
        )

    table_data = (
        {"total_count": len(rows), "page": 1, "page_size": len(rows), "items": rows}
        if include_all_rows
        else _paginate_list(rows, page, page_size)
    )
    table_data["insight"] = largest_insight

    return {
        "refunds_over_time": {
            "total": _money(total_refunds),
            "points": refund_points,
            "insight": over_time_insight,
        },
        "refunds": table_data,
    }


# --------------------------------------------------------------------------- #
# Payment Report
# --------------------------------------------------------------------------- #
def payment_report(
    ctx: ReportContext,
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
    include_all_rows: bool = False,
) -> dict[str, Any]:
    """Payment report: payments received over time and paginated payment rows."""
    period = ctx.period
    lower_bound, upper_bound = day_range(period.start, period.end, ctx.tz)
    payments_qs = ctx.scoped(
        InvoicePayment.objects.filter(
            invoice__organization=ctx.organization,
            is_voided=False,
            payment__status=PaymentStatus.SUCCESS,
            received_on__gte=lower_bound,
            received_on__lt=upper_bound,
        ),
        "invoice__warehouse",
    ).select_related("payment").order_by("-received_on")

    daily_payments = {
        row["slot"].date() if isinstance(row["slot"], datetime) else row["slot"]: row["total"]
        for row in payments_qs.annotate(slot=TruncDate("received_on", tzinfo=ctx.tz))
        .values("slot")
        .annotate(total=Coalesce(Sum("amount"), Value(ZERO), output_field=MONEY))
    }
    chart_granularity = "day" if period.days <= 31 else "week"
    payment_points = _daily_or_bucket_points(ctx, period, chart_granularity, daily_payments)

    total_received = sum(daily_payments.values(), ZERO)
    peak_point = max(payment_points, key=lambda point: Decimal(point["value"]), default=None)
    over_time_insight = None
    if peak_point and Decimal(peak_point["value"]) > ZERO:
        over_time_insight = (
            f"Most was received on {peak_point['label']}, "
            f"at {_money_text(Decimal(peak_point['value']), ctx.currency)}."
        )

    total_count = payments_qs.count()
    if include_all_rows:
        selected_payments = payments_qs
        effective_page = 1
        effective_page_size = total_count
    else:
        offset = (page - 1) * page_size
        selected_payments = payments_qs[offset : offset + page_size]
        effective_page = page
        effective_page_size = page_size

    payment_items = [
        {
            "id": str(invoice_payment.pk),
            "payment_id": invoice_payment.receipt_number,
            "date": invoice_payment.received_on.astimezone(ctx.tz).date().isoformat(),
            "method": invoice_payment.payment.method,
            "method_display": _PAYMENT_METHOD_LABELS.get(
                invoice_payment.payment.method,
                invoice_payment.payment.method.replace("_", " ").title(),
            ),
            "amount": _money(invoice_payment.amount),
        }
        for invoice_payment in selected_payments
    ]

    return {
        "payments_received_over_time": {
            "total": _money(total_received),
            "points": payment_points,
            "insight": over_time_insight,
        },
        "payments": {
            "total_count": total_count,
            "page": effective_page,
            "page_size": effective_page_size,
            "items": payment_items,
        },
    }


# --------------------------------------------------------------------------- #
# Inventory Report (current-state snapshot)
# --------------------------------------------------------------------------- #
def inventory_report(ctx: ReportContext, limit: int = DEFAULT_PAGE_SIZE) -> dict[str, Any]:
    """Inventory report: stock value by category, slow-moving stock, low stock, expiring soon."""
    location_id = ctx.location.pk if ctx.location else None
    inventory_qs = ctx.scoped(
        Inventory.objects.filter(
            product__organization=ctx.organization,
            quantity__gt=0,
        ),
        "warehouse",
    )
    stock_value_expr = ExpressionWrapper(
        F("quantity") * Coalesce(F("batch__cost_price"), F("product__purchase_cost"), Value(ZERO)),
        output_field=MONEY,
    )

    category_rows = list(
        inventory_qs.values("product__category_id", "product__category__name")
        .annotate(value=Coalesce(Sum(stock_value_expr), Value(ZERO), output_field=MONEY))
        .order_by("-value")
    )
    total_stock_worth = sum((row["value"] for row in category_rows), ZERO)
    category_items = [
        {
            "category_id": str(row["product__category_id"]) if row["product__category_id"] else None,
            "name": row["product__category__name"] or "Uncategorized",
            "value": _money(row["value"]),
            "percentage": _percentage(row["value"], total_stock_worth),
        }
        for row in category_rows[:4]
    ]
    others_value = sum((row["value"] for row in category_rows[4:]), ZERO)
    if others_value > ZERO:
        category_items.append(
            {
                "category_id": None,
                "name": "Others",
                "value": _money(others_value),
                "percentage": _percentage(others_value, total_stock_worth),
            }
        )
    stock_category_insight = None
    if category_items and total_stock_worth > ZERO:
        top_category = category_items[0]
        stock_category_insight = (
            f"{top_category['name']} hold the most stock, "
            f"at {_money_text(Decimal(top_category['value']), ctx.currency)}."
        )

    # Slow-moving stock: active tracked products with stock > 0 and no sale in >= 30 days.
    cutoff_date = ctx.today - timedelta(days=SLOW_MOVING_DAYS)
    cutoff_datetime = day_range(cutoff_date, cutoff_date, ctx.tz)[0]

    stocked_products = list(
        Product.objects.filter(
            organization=ctx.organization,
            status=ProductStatusChoices.ACTIVE,
            track_inventory=True,
        )
        .with_stock(ctx.scope, location_id)
        .filter(stock_on_hand__gt=0)
        .values("id", "name", "purchase_cost", "stock_on_hand")
    )
    product_ids = [product["id"] for product in stocked_products]

    last_sold_map = {
        row["product_id"]: row["last_sold_at"]
        for row in ctx.scoped(
            InvoiceItem.objects.filter(
                invoice__organization=ctx.organization,
                product_id__in=product_ids,
                invoice__status__in=SALE_STATUSES,
                invoice__issued_at__isnull=False,
            ).filter(Q(invoice__sale__isnull=True) | Q(invoice__sale__cancelled_at__isnull=True)),
            "invoice__warehouse",
        )
        .values("product_id")
        .annotate(last_sold_at=Max("invoice__issued_at"))
    }
    product_value_map = {
        row["product_id"]: row["value"]
        for row in inventory_qs.filter(product_id__in=product_ids)
        .values("product_id")
        .annotate(value=Coalesce(Sum(stock_value_expr), Value(ZERO), output_field=MONEY))
    }

    slow_moving_entries = []
    for product in stocked_products:
        last_sold = last_sold_map.get(product["id"])
        if last_sold is None or last_sold < cutoff_datetime:
            stock_value = product_value_map.get(
                product["id"],
                Decimal(product["stock_on_hand"]) * Decimal(product["purchase_cost"] or 0),
            )
            days_since_sale = (
                (ctx.today - last_sold.astimezone(ctx.tz).date()).days if last_sold else None
            )
            slow_moving_entries.append(
                {
                    "product_id": str(product["id"]),
                    "name": product["name"],
                    "value": Decimal(stock_value).quantize(CENT, rounding=ROUND_HALF_UP),
                    "days_since_last_sale": days_since_sale,
                }
            )
    slow_moving_entries.sort(key=lambda entry: (-entry["value"], entry["name"]))
    slow_moving_total = sum((entry["value"] for entry in slow_moving_entries), ZERO)
    slow_moving_segments = [
        {
            "product_id": entry["product_id"],
            "name": entry["name"],
            "value": _money(entry["value"]),
            "percentage": _percentage(entry["value"], slow_moving_total),
        }
        for entry in slow_moving_entries[:4]
    ]
    if len(slow_moving_entries) > 4:
        other_slow_value = sum((entry["value"] for entry in slow_moving_entries[4:]), ZERO)
        other_count = len(slow_moving_entries) - 4
        slow_moving_segments.append(
            {
                "product_id": None,
                "name": f"Other {other_count} products",
                "value": _money(other_slow_value),
                "percentage": _percentage(other_slow_value, slow_moving_total),
            }
        )
    slow_moving_insight = None
    if slow_moving_entries:
        top_slow = slow_moving_entries[0]
        if top_slow["days_since_last_sale"] is not None:
            slow_moving_insight = (
                f"{top_slow['name']} has not sold in {top_slow['days_since_last_sale']} days "
                f"and holds {_money_text(top_slow['value'], ctx.currency)} of stock."
            )
        else:
            slow_moving_insight = (
                f"{top_slow['name']} has no recorded sales "
                f"and holds {_money_text(top_slow['value'], ctx.currency)} of stock."
            )

    low_stock_rows = list(
        Product.objects.filter(
            organization=ctx.organization,
            status=ProductStatusChoices.ACTIVE,
            track_inventory=True,
        )
        .with_stock(ctx.scope, location_id)
        .filter(stock_on_hand__lte=F("minimum_stock_level"))
        .order_by("stock_on_hand", "name")
        .values("id", "name", "stock_on_hand", "minimum_stock_level")[:limit]
    )
    low_stock_items = [
        {
            "product_id": str(row["id"]),
            "name": row["name"],
            "in_stock": row["stock_on_hand"],
            "reorder_level": row["minimum_stock_level"],
        }
        for row in low_stock_rows
    ]
    low_stock_insight = None
    if low_stock_items:
        lowest = low_stock_items[0]
        low_stock_insight = f"{lowest['name']} is the lowest, at {lowest['in_stock']} units."

    expiring_rows = list(
        inventory_qs.filter(
            batch__isnull=False,
            batch__expiry_date__gte=ctx.today,
            batch__expiry_date__lte=ctx.today + timedelta(days=EXPIRY_WARNING_DAYS),
        )
        .values("product_id", "product__name", "batch__expiry_date")
        .annotate(units=Sum("quantity"))
        .order_by("batch__expiry_date", "product__name")[:limit]
    )
    expiring_items = [
        {
            "product_id": str(row["product_id"]),
            "name": row["product__name"],
            "expiry_date": row["batch__expiry_date"].isoformat(),
            "units": row["units"],
        }
        for row in expiring_rows
    ]
    expiring_insight = None
    if expiring_items:
        soonest = expiring_rows[0]
        expiring_insight = (
            f"{soonest['product__name']} expires first, "
            f"on {soonest['batch__expiry_date'].strftime('%d %b %Y')}."
        )

    return {
        "stock_value_by_category": {
            "total_stock_worth": _money(total_stock_worth),
            "items": category_items,
            "insight": stock_category_insight,
        },
        "slow_moving_stock": {
            "total_value": _money(slow_moving_total),
            "product_count": len(slow_moving_entries),
            "segments": slow_moving_segments,
            "insight": slow_moving_insight,
        },
        "low_stock": {
            "items": low_stock_items,
            "insight": low_stock_insight,
        },
        "expiring_soon": {
            "items": expiring_items,
            "insight": expiring_insight,
        },
    }
