# App: purchase
from datetime import date

import auto_prefetch
from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from core.helper.enums import PurchaseStatusChoices
from core.helper.enums import UsersRole
from core.helper.media import MediaHelper
from core.helper.models import TimeBasedModel


class Purchase(TimeBasedModel):
    """
    A purchase order / goods-received record from a vendor (PRD §10).

    Only `PurchaseItem.quantity_received` ever enters inventory — never
    `quantity_ordered`. That distinction is enforced in
    `inventory.services.InventoryService.receive_purchase()`, not here.
    """

    organization = auto_prefetch.ForeignKey(
        "users.Organization",
        on_delete=models.CASCADE,
        related_name="purchases",
        help_text=_("Organization this purchase order belongs to."),
    )
    purchase_number = models.CharField(
        max_length=30,
        unique=True,
        editable=False,
        db_index=True,
        help_text=_("Auto-generated sequential reference, e.g. 'PO-2026-0004'."),
    )
    vendor = auto_prefetch.ForeignKey(
        "vendors.Vendor",
        on_delete=models.PROTECT,
        related_name="purchases",
        help_text=_("Vendor this order was placed with."),
    )
    warehouse = auto_prefetch.ForeignKey(
        "warehouse.Warehouse",
        on_delete=models.PROTECT,
        related_name="purchases",
        help_text=_("Warehouse the received goods will be stocked into."),
    )
    ordered_by = auto_prefetch.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="ordered_purchases",
        limit_choices_to=Q(role__in=[UsersRole.OWNER, UsersRole.ADMIN]),
        help_text=_("Admin/Owner who placed this order (PRD §10: Admin-only purchasing)."),
    )
    received_by = auto_prefetch.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="received_purchases",
        help_text=_("User who physically received and checked in the goods."),
    )
    vendor_invoice_number = models.CharField(
        _("Vendor Invoice Number"),
        max_length=100,
        blank=True,
        null=True,
        help_text=_("The vendor's own invoice/reference number for this order."),
    )
    supporting_document = models.FileField(
        upload_to=MediaHelper.get_image_upload_path,
        blank=True,
        null=True,
        help_text=_("Vendor invoice, waybill, or delivery note supporting this purchase."),
    )
    delivery_date = models.DateField(
        blank=True,
        null=True,
        help_text=_("Date the goods were or are expected to be delivered."),
    )
    status = models.CharField(
        max_length=20,
        choices=PurchaseStatusChoices.choices,
        default=PurchaseStatusChoices.DRAFT,
        help_text=_("Lifecycle status of this purchase order."),
    )
    notes = models.TextField(
        blank=True,
        null=True,
        help_text=_("Internal notes about this purchase."),
    )

    class Meta(auto_prefetch.Model.Meta):
        verbose_name = _("Purchase")
        verbose_name_plural = _("Purchases")
        ordering = ["-created_at"]

    def __str__(self):
        return f"Purchase {self.purchase_number} from {self.vendor}"

    @property
    def total_value(self):
        """Value of goods actually received, not merely ordered."""
        return sum(item.received_value for item in self.items.all())

    def save(self, *args, **kwargs):
        if not self.purchase_number:
            year = date.today().year
            last = (
                Purchase.objects.filter(purchase_number__startswith=f"PO-{year}-")
                .order_by("-purchase_number")
                .first()
            )
            last_seq = int(last.purchase_number.split("-")[-1]) if last else 0
            self.purchase_number = f"PO-{year}-{str(last_seq + 1).zfill(4)}"
        super().save(*args, **kwargs)


class PurchaseItem(TimeBasedModel):
    """
    Line item on a Purchase. `purchase_cost` is snapshotted at order time
    so later changes to `Product.purchase_cost` don't rewrite history.
    """

    purchase = auto_prefetch.ForeignKey(
        "purchase.Purchase",
        on_delete=models.CASCADE,
        related_name="items",
        help_text=_("Purchase order this line item belongs to."),
    )
    product = auto_prefetch.ForeignKey(
        "products.Product",
        on_delete=models.PROTECT,
        related_name="purchase_items",
        help_text=_("Product being ordered/received."),
    )
    quantity_ordered = models.PositiveIntegerField(
        help_text=_("Quantity requested from the vendor."),
    )
    quantity_received = models.PositiveIntegerField(
        default=0,
        help_text=_("Quantity actually received so far. Only this enters inventory (PRD §10)."),
    )
    purchase_cost = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text=_("Snapshot of Product.purchase_cost at order time."),
    )

    class Meta(auto_prefetch.Model.Meta):
        verbose_name = _("Purchase Item")
        verbose_name_plural = _("Purchase Items")

    def __str__(self):
        return f"{self.product} (x{self.quantity_ordered})"

    @property
    def received_value(self):
        return self.quantity_received * self.purchase_cost