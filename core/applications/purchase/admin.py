from django import forms
from django.contrib import admin
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from core.applications.purchase.models import (
    Purchase,
    PurchaseItem,
    PurchaseReturn,
    PurchaseReturnItem,
)


class PurchaseAdminForm(forms.ModelForm):
    class Meta:
        model = Purchase
        fields = "__all__"

    def clean(self):
        cleaned_data = super().clean()
        organization = cleaned_data.get("organization")
        vendor = cleaned_data.get("vendor")
        warehouse = cleaned_data.get("warehouse")
        errors = {}
        if organization and vendor and vendor.organization_id != organization.id:
            errors["vendor"] = _("Vendor does not belong to the selected organization.")
        if organization and warehouse and warehouse.organization_id != organization.id:
            errors["warehouse"] = _("Warehouse does not belong to the selected organization.")
        if errors:
            raise ValidationError(errors)
        return cleaned_data


class PurchaseItemInlineForm(forms.ModelForm):
    class Meta:
        model = PurchaseItem
        fields = "__all__"

    def clean(self):
        cleaned_data = super().clean()
        product = cleaned_data.get("product")
        variant = cleaned_data.get("variant")
        unit = cleaned_data.get("unit")
        purchase = cleaned_data.get("purchase") or getattr(self.instance, "purchase", None)
        errors = {}
        if purchase and purchase.organization_id and product and product.organization_id != purchase.organization_id:
            errors["product"] = _("Product does not belong to the purchase order organization.")
        if product and variant and variant.product_id != product.id:
            errors["variant"] = _("Variant does not belong to the selected product.")
        if product and unit and unit.product_id != product.id:
            errors["unit"] = _("Unit conversion does not belong to the selected product.")
        if errors:
            raise ValidationError(errors)
        return cleaned_data


class PurchaseItemInline(admin.TabularInline):
    model = PurchaseItem
    form = PurchaseItemInlineForm
    extra = 1
    fields = (
        "product",
        "variant",
        "unit",
        "quantity_ordered",
        "quantity_received",
        "purchase_cost",
        "discount",
        "received_value_display",
    )
    readonly_fields = ("quantity_received", "received_value_display")
    autocomplete_fields = ("product",)

    @admin.display(description="Received value")
    def received_value_display(self, obj):
        return f"{obj.received_value:,.2f}" if obj.pk else "—"

    def get_readonly_fields(self, request, obj=None):
        if obj is None:
            return ("received_value_display",)
        return self.readonly_fields


@admin.register(Purchase)
class PurchaseAdmin(admin.ModelAdmin):
    form = PurchaseAdminForm
    list_display = (
        "id",
        "purchase_number",
        "vendor",
        "warehouse",
        "status",
        "payment_status",
        "ordered_by",
        "received_by",
        "delivery_date",
        "total_value_display",
        "amount_paid",
        "created_at",
    )
    list_filter = ("status", "payment_status", "warehouse", "vendor")
    search_fields = ("purchase_number", "vendor__company_name", "vendor_invoice_number")
    list_select_related = ("vendor", "warehouse", "ordered_by", "received_by")
    autocomplete_fields = ("vendor", "warehouse", "ordered_by", "received_by")
    readonly_fields = (
        "purchase_number",
        "amount_paid",
        "total_value_display",
        "cancelled_at",
        "cancelled_by",
        "created_at",
        "updated_at",
    )
    date_hierarchy = "delivery_date"
    inlines = [PurchaseItemInline]

    fieldsets = (
        (
            None,
            {
                "fields": (
                    "organization",
                    "purchase_number",
                    "vendor",
                    "warehouse",
                    "status",
                    "payment_status",
                )
            },
        ),
        ("People", {"fields": ("ordered_by", "received_by", "cancelled_by")}),
        (
            "Delivery",
            {
                "fields": (
                    "vendor_invoice_number",
                    "supporting_document",
                    "order_date",
                    "delivery_date",
                    "delivery_method",
                )
            },
        ),
        (
            "Totals",
            {
                "fields": (
                    "other_costs",
                    "discount",
                    "amount_paid",
                    "total_value_display",
                )
            },
        ),
        ("Cancellation", {"fields": ("cancelled_at", "cancel_reason")}),
        ("Notes", {"fields": ("notes",)}),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )

    @admin.display(description="Total received value")
    def total_value_display(self, obj):
        return f"{obj.total_value:,.2f}" if obj.pk else "—"


class PurchaseReturnItemInline(admin.TabularInline):
    model = PurchaseReturnItem
    extra = 0
    readonly_fields = ("purchase_item", "batch", "quantity", "unit_cost", "line_total")


@admin.register(PurchaseReturn)
class PurchaseReturnAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "return_number",
        "purchase",
        "organization",
        "returned_by",
        "total_refund_amount",
        "created_at",
    )
    search_fields = ("return_number", "purchase__purchase_number")
    list_select_related = ("purchase", "organization", "returned_by")
    readonly_fields = ("return_number", "total_refund_amount", "created_at", "updated_at")
    inlines = [PurchaseReturnItemInline]
