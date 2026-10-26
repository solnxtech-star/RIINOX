import django_filters

from core.applications.purchase.models import Purchase
from core.helper.enums import PurchasePaymentStatusChoices, PurchaseStatusChoices


class PurchaseFilterSet(django_filters.FilterSet):
    status = django_filters.ChoiceFilter(choices=PurchaseStatusChoices.choices)
    payment_status = django_filters.ChoiceFilter(choices=PurchasePaymentStatusChoices.choices)
    vendor = django_filters.UUIDFilter(field_name="vendor_id")
    warehouse = django_filters.UUIDFilter(field_name="warehouse_id")
    ordered_by = django_filters.UUIDFilter(field_name="ordered_by_id")
    received_by = django_filters.UUIDFilter(field_name="received_by_id")
    order_date_from = django_filters.DateFilter(field_name="order_date", lookup_expr="gte")
    order_date_to = django_filters.DateFilter(field_name="order_date", lookup_expr="lte")
    delivery_date_from = django_filters.DateFilter(field_name="delivery_date", lookup_expr="gte")
    delivery_date_to = django_filters.DateFilter(field_name="delivery_date", lookup_expr="lte")
    created_from = django_filters.DateTimeFilter(field_name="created_at", lookup_expr="gte")
    created_to = django_filters.DateTimeFilter(field_name="created_at", lookup_expr="lte")

    class Meta:
        model = Purchase
        fields = [
            "status",
            "payment_status",
            "vendor",
            "warehouse",
            "ordered_by",
            "received_by",
            "order_date_from",
            "order_date_to",
            "delivery_date_from",
            "delivery_date_to",
            "created_from",
            "created_to",
        ]

