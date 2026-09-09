from django.contrib import admin

from core.applications.warehouse.models import StockLocation
from core.applications.warehouse.models import Warehouse


class StockLocationInline(admin.TabularInline):
    model = StockLocation
    extra = 0
    fields = ("name", "description", "is_active")
    show_change_link = True


@admin.register(Warehouse)
class WarehouseAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "organization", "manager", "is_active", "location_count")
    list_filter = ("organization", "is_active")
    search_fields = ("name", "code", "address")
    list_select_related = ("organization", "manager")
    autocomplete_fields = ("organization", "manager")
    inlines = [StockLocationInline]

    @admin.display(description="Locations")
    def location_count(self, obj):
        return obj.locations.count()


@admin.register(StockLocation)
class StockLocationAdmin(admin.ModelAdmin):
    list_display = ("name", "warehouse", "is_active", "stocked_product_count")
    list_filter = ("warehouse", "is_active")
    search_fields = ("name", "description", "warehouse__name", "warehouse__code")
    list_select_related = ("warehouse",)
    autocomplete_fields = ("warehouse",)

    @admin.display(description="Products stocked here")
    def stocked_product_count(self, obj):
        # Reverse relation from inventory.Inventory.location — safe even
        # before the inventory app has any rows for this location.
        return obj.inventory_records.count()