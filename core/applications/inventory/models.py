import auto_prefetch
from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from core.helper.enums import ApprovalStatusChoices
from core.helper.enums import PhysicalCountStatusChoices
from core.helper.enums import UsersRole
from core.helper.enums import VarianceReasonChoices
from core.helper.models import TimeBasedModel


class Inventory(TimeBasedModel):
    """
    Current on-hand quantity for a product at a specific warehouse.

    This is a read-optimized projection, not the source of truth — it
    exists so queries don't have to sum the entire InventoryLedgerEntry
    ledger every time. `quantity` is `editable=False` and must only ever
    be mutated inside `InventoryService`, in the same DB transaction that
    writes the corresponding `InventoryLedgerEntry` row, under
    `select_for_update()` to prevent concurrent-sale race conditions.
    """

    product = auto_prefetch.ForeignKey(
        "products.Product",
        on_delete=models.CASCADE,
        related_name="inventory_records",
        help_text=_("Product this stock level is for."),
    )
    variant = auto_prefetch.ForeignKey(
        "products.ProductVariant",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="inventory_records",
        help_text=_("Specific variant of the product, if applicable."),
    )
    warehouse = auto_prefetch.ForeignKey(
        "warehouse.Warehouse",
        on_delete=models.CASCADE,
        related_name="inventory_records",
        help_text=_("Warehouse this stock level applies to."),
    )
    location = auto_prefetch.ForeignKey(
        "warehouse.StockLocation",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="inventory_records",
        help_text=_("Specific shelf/rack this stock is placed at, if tracked."),
    )
    quantity = models.PositiveIntegerField(
        default=0,
        editable=False,
        help_text=_("Current on-hand quantity. System-maintained — see InventoryLedgerEntry."),
    )

    class Meta(auto_prefetch.Model.Meta):
        verbose_name = _("Inventory")
        verbose_name_plural = _("Inventory")
        unique_together = ("product", "variant", "warehouse")
        indexes = [models.Index(fields=["product", "variant", "warehouse"])]

    def __str__(self):
        return f"{self.product} @ {self.warehouse}: {self.quantity}"


class InventoryLedgerEntry(TimeBasedModel):
    """
    The inventory effect of a Transaction. Replaces the old InventoryTransaction model.
    Every quantity change anywhere in the system is represented by exactly one row here.
    """
    transaction = auto_prefetch.ForeignKey(
        "transactions.Transaction",
        on_delete=models.CASCADE,
        related_name="inventory_entries",
    )
    product = auto_prefetch.ForeignKey(
        "products.Product",
        on_delete=models.PROTECT,
        related_name="ledger_entries",
    )
    variant = auto_prefetch.ForeignKey(
        "products.ProductVariant",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ledger_entries",
        help_text=_("Optional specific variant involved in the movement."),
    )
    warehouse = auto_prefetch.ForeignKey(
        "warehouse.Warehouse",
        on_delete=models.PROTECT,
        related_name="ledger_entries",
    )
    quantity_moved = models.IntegerField(
        help_text=_("Positive for stock coming in, negative for stock going out."),
    )
    unit_cost = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text=_("Cost basis per unit at the time of movement, useful for COGS calculation."),
    )

    class Meta(auto_prefetch.Model.Meta):
        verbose_name = _("Inventory Ledger Entry")
        verbose_name_plural = _("Inventory Ledger Entries")

    def __str__(self):
        sign = "+" if self.quantity_moved > 0 else ""
        return f"{self.transaction.transaction_id} - {self.product.name}: {sign}{self.quantity_moved}"



class StockAdjustmentRequest(TimeBasedModel):
    """
    A staff- or system-raised request to change stock outside the normal
    purchase/sale flow (PRD §12, §32). Stays PENDING until Admin approves
    or rejects it; only on approval does the service layer create the
    corresponding InventoryLedgerEntry.
    """

    product = auto_prefetch.ForeignKey(
        "products.Product",
        on_delete=models.CASCADE,
        related_name="adjustment_requests",
        help_text=_("Product the adjustment applies to."),
    )
    variant = auto_prefetch.ForeignKey(
        "products.ProductVariant",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="adjustment_requests",
        help_text=_("Variant the adjustment applies to, if applicable."),
    )
    warehouse = auto_prefetch.ForeignKey(
        "warehouse.Warehouse",
        on_delete=models.CASCADE,
        related_name="adjustment_requests",
        help_text=_("Warehouse the adjustment applies to."),
    )
    requested_quantity_change = models.IntegerField(
        help_text=_("Signed: positive to add stock, negative to remove."),
    )
    reason = models.TextField(
        help_text=_("Why this adjustment is being requested."),
    )
    requested_by = auto_prefetch.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="requested_stock_adjustments",
        help_text=_("User who raised this request."),
    )
    status = models.CharField(
        max_length=20,
        choices=ApprovalStatusChoices.choices,
        default=ApprovalStatusChoices.PENDING,
        help_text=_("Approval state — only APPROVED requests result in a stock movement."),
    )
    reviewed_by = auto_prefetch.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_stock_adjustments",
        limit_choices_to=Q(role__in=[UsersRole.OWNER, UsersRole.ADMIN]),
        help_text=_("Admin/Owner who approved or rejected this request."),
    )
    reviewed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("When this request was approved or rejected."),
    )
    rejection_reason = models.TextField(
        blank=True,
        null=True,
        help_text=_("Required explanation when the request is rejected."),
    )

    class Meta(auto_prefetch.Model.Meta):
        verbose_name = _("Stock Adjustment Request")
        verbose_name_plural = _("Stock Adjustment Requests")
        ordering = ["-created_at"]

    def __str__(self):
        return f"Adjustment {self.requested_quantity_change} — {self.product} ({self.status})"


class PhysicalStockCount(TimeBasedModel):
    """A physical stock count session for a warehouse (PRD §11)."""

    warehouse = auto_prefetch.ForeignKey(
        "warehouse.Warehouse",
        on_delete=models.CASCADE,
        related_name="stock_counts",
        help_text=_("Warehouse being counted."),
    )
    counted_by = auto_prefetch.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="stock_counts",
        help_text=_("User who performed the physical count."),
    )
    count_date = models.DateField(
        help_text=_("Date the physical count was carried out."),
    )
    status = models.CharField(
        max_length=20,
        choices=PhysicalCountStatusChoices.choices,
        default=PhysicalCountStatusChoices.DRAFT,
        help_text=_("DRAFT while counting is in progress; COMPLETED once finalized."),
    )
    notes = models.TextField(
        blank=True,
        null=True,
        help_text=_("Any general notes about this count session."),
    )

    class Meta(auto_prefetch.Model.Meta):
        verbose_name = _("Physical Stock Count")
        verbose_name_plural = _("Physical Stock Counts")
        ordering = ["-count_date"]

    def __str__(self):
        return f"Count @ {self.warehouse} on {self.count_date}"


class PhysicalStockCountItem(TimeBasedModel):
    """
    A single product's expected-vs-counted line within a
    PhysicalStockCount. `expected_quantity` is snapshotted from Inventory
    at count time so it can't drift if stock moves mid-count.
    """

    count = auto_prefetch.ForeignKey(
        "inventory.PhysicalStockCount",
        on_delete=models.CASCADE,
        related_name="items",
        help_text=_("The count session this line belongs to."),
    )
    product = auto_prefetch.ForeignKey(
        "products.Product",
        on_delete=models.PROTECT,
        related_name="stock_count_items",
        help_text=_("Product being counted."),
    )
    variant = auto_prefetch.ForeignKey(
        "products.ProductVariant",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="stock_count_items",
        help_text=_("Variant being counted, if applicable."),
    )
    expected_quantity = models.IntegerField(
        help_text=_("Snapshot of Inventory.quantity at the moment counting started."),
    )
    counted_quantity = models.IntegerField(
        help_text=_("Quantity physically counted on the warehouse floor."),
    )
    variance_reason = models.CharField(
        max_length=20,
        choices=VarianceReasonChoices.choices,
        blank=True,
        null=True,
        help_text=_("Required when counted_quantity differs from expected_quantity."),
    )
    notes = models.TextField(
        blank=True,
        null=True,
        help_text=_("Additional detail on the variance, if any."),
    )

    class Meta(auto_prefetch.Model.Meta):
        verbose_name = _("Physical Stock Count Item")
        verbose_name_plural = _("Physical Stock Count Items")

    def __str__(self):
        return f"{self.product}: expected {self.expected_quantity}, counted {self.counted_quantity}"

    @property
    def variance(self):
        return self.counted_quantity - self.expected_quantity
