from django.contrib import admin

from core.applications.purchase.models import Purchase
from core.applications.purchase.models import PurchaseItem


class PurchaseItemInline(admin.TabularInline):
    model = PurchaseItem
    extra = 1
    fields = (
        "product", "quantity_ordered",
        "quantity_received", "purchase_cost",
        "received_value_display",
    )
    readonly_fields = ("quantity_received", "received_value_display")
    autocomplete_fields = ("product",)

    @admin.display(description="Received value")
    def received_value_display(self, obj):
        return f"{obj.received_value:,.2f}" if obj.pk else "—"

    def get_readonly_fields(self, request, obj=None):
        # A brand-new Purchase has no items yet, so there's nothing to
        # protect — let quantity_received default to 0 normally. Once the
        # item row exists, lock it (see module docstring).
        if obj is None:
            return ("received_value_display",)
        return self.readonly_fields


@admin.register(Purchase)
class PurchaseAdmin(admin.ModelAdmin):
    list_display = (
        "purchase_number",
        "vendor",
        "warehouse",
        "status",
        "ordered_by",
        "received_by",
        "delivery_date",
        "total_value_display",
        "created_at",
    )
    list_filter = ("status", "warehouse", "vendor")
    search_fields = ("purchase_number", "vendor__company_name", "vendor_invoice_number")
    list_select_related = ("vendor", "warehouse", "ordered_by", "received_by")
    autocomplete_fields = ("vendor", "warehouse", "ordered_by", "received_by")
    readonly_fields = ("purchase_number", "total_value_display", "created_at", "updated_at")
    date_hierarchy = "delivery_date"
    inlines = [PurchaseItemInline]

    fieldsets = (
        (None, {"fields": ("organization", "purchase_number", "vendor", "warehouse", "status")}),
        ("People", {"fields": ("ordered_by", "received_by")}),
        ("Delivery", {"fields": ("vendor_invoice_number", "supporting_document", "delivery_date")}),
        ("Totals", {"fields": ("total_value_display",)}),
        ("Notes", {"fields": ("notes",)}),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )

    @admin.display(description="Total received value")
    def total_value_display(self, obj):
        return f"{obj.total_value:,.2f}" if obj.pk else "—"