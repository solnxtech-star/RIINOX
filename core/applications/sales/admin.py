from __future__ import annotations

from django.contrib import admin
from django.utils.translation import gettext_lazy as _
from unfold.admin import ModelAdmin, TabularInline
from unfold.decorators import display

from core.applications.sales.models import Sale, SaleItem, SaleReturn, SaleReturnItem


class SaleItemInline(TabularInline):
    model = SaleItem
    extra = 0
    fields = (
        "product",
        "variant",
        "product_name",
        "product_sku",
        "quantity",
        "returned_quantity",
        "unit_name",
        "unit_multiplier",
        "unit_price",
        "discount",
        "discount_type",
        "tax",
        "tax_rate",
        "line_total",
    )
    readonly_fields = ("returned_quantity", "line_total")

    @display(description=_("Line Total"))
    def line_total(self, obj: SaleItem | None = None):
        if not obj or not getattr(obj, "pk", None) or obj.unit_price is None:
            return "-"
        return f"{obj.total:,.2f}"


class SaleReturnItemInline(TabularInline):
    model = SaleReturnItem
    extra = 0
    fields = (
        "sale_item",
        "quantity",
        "refund_amount",
        "condition",
        "restock_action",
    )


@admin.register(Sale)
class SaleAdmin(ModelAdmin):
    list_display = (
        "sale_id",
        "organization",
        "customer",
        "warehouse",
        "sales_rep",
        "payment_status",
        "fulfillment_status",
        "currency",
        "total",
        "created_at",
    )
    list_filter = (
        "organization",
        "payment_status",
        "fulfillment_status",
        "currency",
        "created_at",
    )
    search_fields = (
        "sale_id",
        "reference_number",
        "customer__name",
        "customer__company_name",
    )
    list_select_related = (
        "organization",
        "customer",
        "warehouse",
        "location",
        "sales_rep",
    )
    readonly_fields = (
        "created_at",
        "updated_at",
        "cancelled_at",
        "cancelled_by",
    )
    inlines = [SaleItemInline]


@admin.register(SaleItem)
class SaleItemAdmin(ModelAdmin):
    list_display = (
        "id",
        "sale",
        "product",
        "product_name",
        "product_sku",
        "quantity",
        "returned_quantity",
        "unit_price",
        "total_display",
        "created_at",
    )
    list_filter = ("unit_name", "discount_type", "created_at")
    search_fields = (
        "product_name",
        "product_sku",
        "sale__sale_id",
    )
    list_select_related = ("sale", "product", "variant")
    readonly_fields = ("created_at", "updated_at", "total_display")

    @display(description=_("Total"))
    def total_display(self, obj: SaleItem | None = None):
        if not obj or not getattr(obj, "pk", None) or obj.unit_price is None:
            return "-"
        return f"{obj.total:,.2f}"


@admin.register(SaleReturn)
class SaleReturnAdmin(ModelAdmin):
    list_display = (
        "id",
        "original_sale",
        "organization",
        "refund_amount",
        "refund_method",
        "status",
        "created_at",
    )
    list_filter = (
        "organization",
        "refund_method",
        "status",
        "created_at",
    )
    search_fields = (
        "original_sale__sale_id",
        "return_reason",
    )
    list_select_related = (
        "original_sale",
        "organization",
    )
    readonly_fields = ("created_at", "updated_at")
    inlines = [SaleReturnItemInline]


@admin.register(SaleReturnItem)
class SaleReturnItemAdmin(ModelAdmin):
    list_display = (
        "id",
        "sale_return",
        "sale_item",
        "quantity",
        "refund_amount",
        "condition",
        "restock_action",
        "created_at",
    )
    list_filter = ("condition", "restock_action", "created_at")
    search_fields = (
        "sale_return__original_sale__sale_id",
        "sale_item__product_name",
    )
    list_select_related = ("sale_return", "sale_item")
    readonly_fields = ("created_at", "updated_at")
