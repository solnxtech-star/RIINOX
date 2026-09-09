from django.contrib import admin
from django.db.models import F
from django.db.models import Sum

from core.applications.vendors.models import Vendor


@admin.register(Vendor)
class VendorAdmin(admin.ModelAdmin):
    list_display = (
        "company_name",
        "contact_person",
        "phone",
        "email",
        "status",
        "organization",
        "purchase_count",
        "total_purchases_display",
    )
    list_filter = ("status", "organization")
    search_fields = ("company_name", "contact_person", "phone", "email")
    list_select_related = ("organization",)
    autocomplete_fields = ("organization",)
    filter_horizontal = ("products_supplied",)

    fieldsets = (
        (None, {"fields": ("organization", "company_name", "status")}),
        ("Contact", {"fields": ("contact_person", "phone", "email", "address")}),
        ("Catalogue", {"fields": ("products_supplied",)}),
        ("Notes", {"fields": ("notes",)}),
    )

    @admin.display(description="Purchases")
    def purchase_count(self, obj):
        return obj.purchases.count()

    @admin.display(description="Total received (₦)")
    def total_purchases_display(self, obj):
        # Value of goods actually received from this vendor, not merely
        # ordered — mirrors Purchase.total_value's own logic (PRD §10).
        total = obj.purchases.aggregate(
            total=Sum(F("items__quantity_received") * F("items__purchase_cost")),
        )["total"]
        return f"{total or 0:,.2f}"