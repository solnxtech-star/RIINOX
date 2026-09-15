from django.contrib import admin
from django.contrib.contenttypes.admin import GenericTabularInline
from django.urls import NoReverseMatch
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html

from core.applications.inventory.models import Inventory
from core.applications.inventory.models import PhysicalStockCount
from core.applications.inventory.models import PhysicalStockCountItem
from core.applications.inventory.models import StockAdjustmentRequest


@admin.register(Inventory)
class InventoryAdmin(admin.ModelAdmin):
    """
    Read-only by design. Rows should only ever be created/updated by
    InventoryService as a side effect of a real InventoryLedgerEntry —
    letting anyone edit `quantity` here is exactly the "silent stock
    change" the PRD forbids (§9).
    """

    list_display = ("product", "warehouse", "location", "quantity", "updated_at")
    list_filter = ("warehouse", "location")
    search_fields = (
        "product__name",
        "product__sku",
        "product__barcode",
        "warehouse__name",
        "warehouse__code",
    )
    list_select_related = ("product", "warehouse", "location")
    readonly_fields = ("product", "warehouse", "location", "quantity", "created_at", "updated_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(StockAdjustmentRequest)
class StockAdjustmentRequestAdmin(admin.ModelAdmin):
    """
    The one place in this app where Admin actually changes an outcome —
    approving or rejecting a pending adjustment (PRD §12, §32). `status`
    is readonly on the form itself; transitions only happen through the
    actions below, so every approval/rejection is attributable to
    whichever Admin ran the action, at the time they ran it — not to
    whoever last saved the form.

    NOTE: these actions record the *decision*. Actually writing the
    resulting InventoryLedgerEntry belongs in InventoryService (so a
    signal or service call should hook in here once that layer exists) —
    intentionally not duplicated in the admin layer.
    """

    list_display = (
        "product",
        "warehouse",
        "requested_quantity_change",
        "status",
        "requested_by",
        "reviewed_by",
        "reviewed_at",
        "created_at",
    )
    list_filter = ("status", "warehouse")
    search_fields = ("product__name", "product__sku", "reason", "requested_by__email")
    list_select_related = ("product", "warehouse", "requested_by", "reviewed_by")
    readonly_fields = ("status", "requested_by", "reviewed_by", "reviewed_at", "created_at")
    actions = ["approve_requests", "reject_requests"]

    @admin.action(description="Approve selected adjustment requests")
    def approve_requests(self, request, queryset):
        pending = queryset.filter(status="pending")
        updated = pending.update(
            status="approved",
            reviewed_by=request.user,
            reviewed_at=timezone.now(),
        )
        skipped = queryset.count() - pending.count()
        self.message_user(request, f"Approved {updated} request(s).")
        if skipped:
            self.message_user(request, f"Skipped {skipped} request(s) not in PENDING status.")

    @admin.action(description="Reject selected adjustment requests")
    def reject_requests(self, request, queryset):
        pending = queryset.filter(status="pending")
        missing_reason = pending.filter(rejection_reason__isnull=True) | pending.filter(rejection_reason="")
        if missing_reason.exists():
            self.message_user(
                request,
                "Every selected request needs a rejection_reason filled in on its "
                "own change form before it can be rejected in bulk.",
                level="error",
            )
            return
        updated = pending.update(
            status="rejected",
            reviewed_by=request.user,
            reviewed_at=timezone.now(),
        )
        self.message_user(request, f"Rejected {updated} request(s).")


class PhysicalStockCountItemInline(admin.TabularInline):
    model = PhysicalStockCountItem
    extra = 0
    fields = ("product", "expected_quantity", "counted_quantity", "variance_display", "variance_reason", "notes")
    readonly_fields = ("variance_display",)
    autocomplete_fields = ("product",)

    @admin.display(description="Variance")
    def variance_display(self, obj):
        return obj.variance if obj.pk else "—"

    def get_readonly_fields(self, request, obj=None):
        # Once a count exists, its expected_quantity is a frozen snapshot —
        # don't let it be hand-edited after the fact (PRD §11).
        if obj and obj.pk:
            return self.readonly_fields + ("expected_quantity",)
        return self.readonly_fields


@admin.register(PhysicalStockCount)
class PhysicalStockCountAdmin(admin.ModelAdmin):
    list_display = ("warehouse", "count_date", "counted_by", "status", "item_count")
    list_filter = ("warehouse", "status")
    search_fields = ("warehouse__name", "warehouse__code", "notes")
    list_select_related = ("warehouse", "counted_by")
    date_hierarchy = "count_date"
    inlines = [PhysicalStockCountItemInline]

    @admin.display(description="Items counted")
    def item_count(self, obj):
        return obj.items.count()


@admin.register(PhysicalStockCountItem)
class PhysicalStockCountItemAdmin(admin.ModelAdmin):
    """
    Registered standalone too (in addition to the inline above) for
    cross-count variance investigation — e.g. "show me every DAMAGED
    variance across all warehouses this month" (PRD §19, §40).
    """

    list_display = ("count", "product", "expected_quantity", "counted_quantity", "variance_display", "variance_reason")
    list_filter = ("variance_reason", "count__warehouse")
    search_fields = ("product__name", "product__sku", "notes")
    list_select_related = ("count", "product", "count__warehouse")

    @admin.display(description="Variance")
    def variance_display(self, obj):
        return obj.variance
