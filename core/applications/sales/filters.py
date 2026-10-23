from __future__ import annotations

import django_filters
from django.db.models import Q

from core.applications.sales.models import Sale, SaleReturn


class SaleFilterSet(django_filters.FilterSet):
    """
    FilterSet for the Sale model supporting status, date range, amount bounds,
    and cross-field text searching.
    """
    customer = django_filters.UUIDFilter(field_name="customer_id")
    location = django_filters.UUIDFilter(field_name="location_id")
    sales_rep = django_filters.UUIDFilter(field_name="sales_rep_id")
    payment_status = django_filters.CharFilter(field_name="payment_status")
    fulfillment_status = django_filters.CharFilter(field_name="fulfillment_status")
    currency = django_filters.CharFilter(field_name="currency")

    # Date ranges
    created_after = django_filters.DateTimeFilter(field_name="created_at", lookup_expr="gte")
    created_before = django_filters.DateTimeFilter(field_name="created_at", lookup_expr="lte")
    due_date_after = django_filters.DateFilter(field_name="due_date", lookup_expr="gte")
    due_date_before = django_filters.DateFilter(field_name="due_date", lookup_expr="lte")

    # Amount bounds
    min_total = django_filters.NumberFilter(field_name="total", lookup_expr="gte")
    max_total = django_filters.NumberFilter(field_name="total", lookup_expr="lte")

    # Search keyword
    search = django_filters.CharFilter(method="filter_search")

    class Meta:
        model = Sale
        fields = [
            "customer",
            "location",
            "sales_rep",
            "payment_status",
            "fulfillment_status",
            "currency",
            "created_after",
            "created_before",
            "due_date_after",
            "due_date_before",
            "min_total",
            "max_total",
            "search",
        ]

    def filter_search(self, queryset, name, value):
        if not value:
            return queryset
        return queryset.filter(
            Q(sale_id__icontains=value)
            | Q(reference_number__icontains=value)
            | Q(customer__name__icontains=value)
        )


class SaleReturnFilterSet(django_filters.FilterSet):
    """
    FilterSet for the SaleReturn model.
    """
    original_sale = django_filters.UUIDFilter(field_name="original_sale_id")
    status = django_filters.CharFilter(field_name="status")
    refund_method = django_filters.CharFilter(field_name="refund_method")

    created_after = django_filters.DateTimeFilter(field_name="created_at", lookup_expr="gte")
    created_before = django_filters.DateTimeFilter(field_name="created_at", lookup_expr="lte")

    min_refund = django_filters.NumberFilter(field_name="refund_amount", lookup_expr="gte")
    max_refund = django_filters.NumberFilter(field_name="refund_amount", lookup_expr="lte")

    class Meta:
        model = SaleReturn
        fields = [
            "original_sale",
            "status",
            "refund_method",
            "created_after",
            "created_before",
            "min_refund",
            "max_refund",
        ]
