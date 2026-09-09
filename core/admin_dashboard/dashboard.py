from datetime import timedelta
from decimal import Decimal

from django.db.models import Count
from django.db.models import DecimalField
from django.db.models import F
from django.db.models import Q
from django.db.models import Sum
from django.db.models import Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from core.applications.inventory.models import InventoryTransaction
from core.applications.inventory.models import PhysicalStockCountItem
from core.applications.invoice.models import Invoice
from core.applications.invoice.models import InvoiceItem
from core.applications.products.models import Product
from core.helper.enums import UsersRole


def _organization(request):
    """
    Resolve the organization associated with the current user.

    Assumes request.user.organization is a direct FK.
    """
    return getattr(request.user, "organization", None)


def _is_privileged(user):
    """
    Check whether the user has permission to view admin-only KPIs.
    """
    return (
        user.is_superuser
        or getattr(user, "role", None)
        in (UsersRole.OWNER, UsersRole.ADMIN)
    )


def _decimal_zero():
    """
    Return a Decimal zero explicitly typed as a DecimalField.

    This prevents Django from mixing DecimalField and IntegerField
    when using Coalesce().
    """
    return Value(
        Decimal("0.00"),
        output_field=DecimalField(),
    )


def dashboard_callback(request, context):
    org = _organization(request)
    privileged = _is_privileged(request.user)

    today = timezone.localdate()
    thirty_days_ago = today - timedelta(days=30)

    # ------------------------------------------------------------------
    # Base querysets
    # ------------------------------------------------------------------

    product_qs = Product.objects.all()
    invoice_qs = Invoice.objects.all()
    transaction_qs = InventoryTransaction.objects.all()

    if org is not None:
        product_qs = product_qs.filter(
            organization=org,
        )

        invoice_qs = invoice_qs.filter(
            organization=org,
        )

        transaction_qs = transaction_qs.filter(
            product__organization=org,
        )

    # ------------------------------------------------------------------
    # Stock health
    # ------------------------------------------------------------------

    stock_health = product_qs.aggregate(
        total_products=Count("id"),

        total_stock=Coalesce(
            Sum("current_stock"),
            0,
        ),

        low_stock=Count(
            "id",
            filter=Q(
                current_stock__gt=0,
                current_stock__lte=F("minimum_stock_level"),
            ),
        ),

        out_of_stock=Count(
            "id",
            filter=Q(current_stock=0),
        ),
    )

    # ------------------------------------------------------------------
    # Today's invoices
    # ------------------------------------------------------------------

    todays_invoices = invoice_qs.filter(
        created_at__date=today,
        is_voided=False,
    )

    # ------------------------------------------------------------------
    # Today's sales
    # ------------------------------------------------------------------

    todays_sales = todays_invoices.aggregate(
        total=Coalesce(
            Sum("total"),
            _decimal_zero(),
            output_field=DecimalField(),
        )
    )["total"]

    todays_invoice_count = todays_invoices.count()

    # ------------------------------------------------------------------
    # Outstanding payments
    # ------------------------------------------------------------------

    outstanding_payments = (
        invoice_qs
        .filter(is_voided=False)
        .exclude(status="paid")
        .aggregate(
            total=Coalesce(
                Sum("total"),
                _decimal_zero(),
                output_field=DecimalField(),
            )
        )["total"]
    )

    # ------------------------------------------------------------------
    # Sales by staff
    # ------------------------------------------------------------------

    sales_by_staff = list(
        todays_invoices
        .values("sales_rep__email")
        .annotate(
            total=Sum("total"),
            count=Count("id"),
        )
        .order_by("-total")[:5]
    )

    # ------------------------------------------------------------------
    # Top products - last 30 days
    # ------------------------------------------------------------------

    top_products_qs = InvoiceItem.objects.filter(
        invoice__created_at__date__gte=thirty_days_ago,
        invoice__is_voided=False,
    )

    # Important: keep dashboard data within the current organization.
    if org is not None:
        top_products_qs = top_products_qs.filter(
            invoice__organization=org,
        )

    top_products = list(
        top_products_qs
        .values("product__name")
        .annotate(
            quantity_sold=Sum("quantity"),
        )
        .order_by("-quantity_sold")[:5]
    )

    # ------------------------------------------------------------------
    # Today's inventory movements
    # ------------------------------------------------------------------

    todays_movements = transaction_qs.filter(
        created_at__date=today,
    ).count()

    # ------------------------------------------------------------------
    # Recent stock variances
    # ------------------------------------------------------------------

    recent_variances = list(
        PhysicalStockCountItem.objects
        .exclude(
            counted_quantity=F("expected_quantity"),
        )
        .select_related(
            "product",
            "count__warehouse",
        )
        .order_by("-created_at")[:5]
    )

    # ------------------------------------------------------------------
    # Recent inventory activity
    # ------------------------------------------------------------------

    recent_activity = list(
        transaction_qs
        .select_related(
            "product",
            "warehouse",
            "performed_by",
        )
        .order_by("-created_at")[:8]
    )

    # ------------------------------------------------------------------
    # Common dashboard context
    # ------------------------------------------------------------------

    context.update(
        {
            "is_privileged": privileged,

            "kpi_total_products": stock_health["total_products"],
            "kpi_total_stock": stock_health["total_stock"],
            "kpi_low_stock": stock_health["low_stock"],
            "kpi_out_of_stock": stock_health["out_of_stock"],

            "kpi_todays_sales": todays_sales,
            "kpi_todays_invoices": todays_invoice_count,
            "kpi_outstanding_payments": outstanding_payments,
            "kpi_todays_movements": todays_movements,

            "sales_by_staff": sales_by_staff,
            "top_products": top_products,
            "recent_variances": recent_variances,
            "recent_activity": recent_activity,
        }
    )

    # ------------------------------------------------------------------
    # Admin-only financial KPIs
    # ------------------------------------------------------------------

    if privileged:

        # Inventory valuation:
        # current_stock × purchase_cost
        inventory_valuation = product_qs.aggregate(
            valuation=Coalesce(
                Sum(
                    F("current_stock") * F("purchase_cost"),
                    output_field=DecimalField(),
                ),
                _decimal_zero(),
                output_field=DecimalField(),
            )
        )["valuation"]

        # Cost of goods sold for today's invoices
        todays_cogs = InvoiceItem.objects.filter(
            invoice__in=todays_invoices,
        ).aggregate(
            cogs=Coalesce(
                Sum(
                    F("quantity") * F("product__purchase_cost"),
                    output_field=DecimalField(),
                ),
                _decimal_zero(),
                output_field=DecimalField(),
            )
        )["cogs"]

        todays_profit = todays_sales - todays_cogs

        context.update(
            {
                "kpi_inventory_valuation": inventory_valuation,
                "kpi_todays_profit": todays_profit,
            }
        )

    return context