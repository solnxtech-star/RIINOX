from __future__ import annotations

from rest_framework import serializers

from core.applications.dashboard.period import PERIODS


class ReportPeriodMetaSerializer(serializers.Serializer):
    key = serializers.CharField()
    label = serializers.CharField()
    start_date = serializers.DateField()
    end_date = serializers.DateField()
    previous_start_date = serializers.DateField()
    previous_end_date = serializers.DateField()


class ReportLocationMetaSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()


class ReportMetaSerializer(serializers.Serializer):
    period = ReportPeriodMetaSerializer()
    location = ReportLocationMetaSerializer(allow_null=True)
    all_locations = serializers.BooleanField()
    currency = serializers.CharField()
    timezone = serializers.CharField()
    has_profit_permission = serializers.BooleanField()


class ReportQuerySerializer(serializers.Serializer):
    period = serializers.ChoiceField(choices=list(PERIODS.keys()), required=False)
    start_date = serializers.DateField(required=False)
    end_date = serializers.DateField(required=False)
    location = serializers.UUIDField(required=False)
    granularity = serializers.ChoiceField(choices=["daily", "weekly", "monthly"], required=False)
    page = serializers.IntegerField(min_value=1, required=False)
    page_size = serializers.IntegerField(min_value=1, max_value=100, required=False)


class ExportQuerySerializer(ReportQuerySerializer):
    export_format = serializers.ChoiceField(choices=["json", "csv"], required=False, default="json")


class ReportOverviewResponseSerializer(serializers.Serializer):
    meta = ReportMetaSerializer()
    gross_profit_over_time = serializers.DictField(allow_null=True)
    sales_by_category = serializers.DictField()
    payment_status_and_methods = serializers.DictField()
    browse_reports = serializers.ListField(child=serializers.DictField())


class SalesReportResponseSerializer(serializers.Serializer):
    meta = ReportMetaSerializer()
    sales_over_time = serializers.DictField()
    top_products_by_sales = serializers.DictField()
    top_customers_by_sales = serializers.DictField()


class CustomerReportResponseSerializer(serializers.Serializer):
    meta = ReportMetaSerializer()
    new_customers_over_time = serializers.DictField()
    outstanding_customer_balances = serializers.DictField()
    sales_by_customer_type = serializers.DictField()


class SupplierReportResponseSerializer(serializers.Serializer):
    meta = ReportMetaSerializer()
    purchases_over_time = serializers.DictField()
    spending_by_supplier = serializers.DictField()
    outstanding_supplier_balances = serializers.DictField()


class RefundReportResponseSerializer(serializers.Serializer):
    meta = ReportMetaSerializer()
    refunds_over_time = serializers.DictField()
    refunds = serializers.DictField()


class PaymentReportResponseSerializer(serializers.Serializer):
    meta = ReportMetaSerializer()
    payments_received_over_time = serializers.DictField()
    payments = serializers.DictField()


class InventoryReportResponseSerializer(serializers.Serializer):
    meta = ReportMetaSerializer()
    stock_value_by_category = serializers.DictField()
    slow_moving_stock = serializers.DictField()
    low_stock = serializers.DictField()
    expiring_soon = serializers.DictField()
