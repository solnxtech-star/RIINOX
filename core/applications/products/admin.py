from django.contrib import admin
from unfold.admin import ModelAdmin
from unfold.decorators import display

from core.applications.products.models import Brand
from core.applications.products.models import Category
from core.applications.products.models import Product
from core.applications.products.models import ProductImage
from core.helper.enums import UsersRole


@admin.register(Category)
class CategoryAdmin(ModelAdmin):
    list_display = ("name", "organization", "is_active", "product_count")
    list_filter = ("organization", "is_active")
    search_fields = ("name", "description")
    list_select_related = ("organization",)

    @admin.display(description="Products")
    def product_count(self, obj):
        return obj.products.count()


@admin.register(Brand)
class BrandAdmin(ModelAdmin):
    list_display = ("name", "organization", "is_active", "product_count")
    list_filter = ("organization", "is_active")
    search_fields = ("name",)
    list_select_related = ("organization",)

    @admin.display(description="Products")
    def product_count(self, obj):
        return obj.products.count()

class ProductImageInline(admin.StackedInline):
    model = ProductImage
    extra = 1
    fields = ("image", "is_primary", "sort_order")

@admin.register(Product)
class ProductAdmin(ModelAdmin):
    list_filter = ("status", "category", "brand", "organization", "unit_of_measurement")
    search_fields = ("name", "sku", "barcode")
    list_select_related = ("category", "brand", "organization", "created_by")
    autocomplete_fields = ("category", "brand", "organization", "created_by")

    fieldsets_common = (
        (None, {"fields": ("organization", "name", "sku", "barcode", "category", "brand", "status_badge")}),
        ("Description", {"fields": ("description", "quality_grade", "unit_of_measurement", "image")}),
        ("Customer-facing price", {"fields": ("customer_sale_price",)}),
        ("Stock", {"fields": ("minimum_stock_level", "opening_stock", "current_stock")}),
    )
    fieldsets_admin_only = (
        ("Admin-only pricing", {"fields": ("purchase_cost", "vendor_sale_cost")}),
        ("Ownership", {"fields": ("created_by", "created_at", "updated_at")}),
    )

    @staticmethod
    def _is_privileged(user):
        return user.is_superuser or getattr(user, "role", None) in (UsersRole.OWNER, UsersRole.ADMIN)

    def get_fieldsets(self, request, obj=None):
        if self._is_privileged(request.user):
            return self.fieldsets_common + self.fieldsets_admin_only
        return self.fieldsets_common

    def get_list_display(self, request):
        base = ("name", "sku", "category", "brand", "customer_sale_price", "current_stock", "status_badge")
        if self._is_privileged(request.user):
            return base + ("purchase_cost", "vendor_sale_cost")
        return base

    def get_readonly_fields(self, request, obj=None):
        readonly = ["current_stock", "created_at", "updated_at", "status_badge"]
        # opening_stock is a one-time figure recorded at creation — once the
        # product exists, treat it the same as current_stock: history, not
        # something to quietly retype later.
        if obj is not None:
            readonly.append("opening_stock")
        return readonly

    @display(description="Status", label=True)
    def status_badge(self, obj):
        colors = {
            "active": "success",
            "inactive": "warning",
            "discontinued": "danger",
            "archived": "info",
        }
        return obj.status, colors.get(obj.status, "info")
    
    