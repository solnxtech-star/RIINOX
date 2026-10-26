from datetime import date
from decimal import Decimal

import auto_prefetch
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from core.applications.purchase.queryset import (
    PurchaseItemQuerySet,
    PurchaseQuerySet,
    PurchaseReturnItemQuerySet,
    PurchaseReturnQuerySet,
)
from core.helper.enums import PurchasePaymentStatusChoices, PurchaseStatusChoices, UsersRole
from core.helper.media import MediaHelper
from core.helper.models import TimeBasedModel


class Purchase(TimeBasedModel):
    objects = PurchaseQuerySet.as_manager()

    """
    A purchase order / goods-received record from a vendor (PRD §10, §18).

    Only `PurchaseItem.quantity_received` ever enters inventory — never
    `quantity_ordered`. That distinction is enforced in
    `purchase.services.receive_purchase_order()`.
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
        limit_choices_to=Q(memberships__role__slug__in=[UsersRole.OWNER, UsersRole.ADMIN]),
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
    order_date = models.DateField(
        blank=True,
        null=True,
        help_text=_("Date the order was placed."),
    )
    delivery_method = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        help_text=_("Delivery method used for the order (e.g. Supplier Delivery)."),
    )
    other_costs = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        help_text=_("Additional costs such as delivery or handling."),
    )
    discount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        help_text=_("Global discount applied to the purchase order."),
    )
    status = models.CharField(
        max_length=20,
        choices=PurchaseStatusChoices.choices,
        default=PurchaseStatusChoices.DRAFT,
        help_text=_("Lifecycle status of this purchase order."),
    )
    amount_paid = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        editable=False,
        help_text=_("Total paid to the vendor against this purchase order."),
    )
    payment_status = models.CharField(
        max_length=20,
        choices=PurchasePaymentStatusChoices.choices,
        default=PurchasePaymentStatusChoices.UNPAID,
        help_text=_("Payment status of this purchase order."),
    )
    cancelled_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("Timestamp when this purchase order was cancelled."),
    )
    cancelled_by = auto_prefetch.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cancelled_purchases",
        help_text=_("User who cancelled this purchase order."),
    )
    cancel_reason = models.TextField(
        blank=True,
        null=True,
        help_text=_("Reason provided when cancelling this purchase order."),
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

    def clean(self) -> None:
        super().clean()
        errors = {}
        if self.vendor_id and self.organization_id and self.vendor.organization_id != self.organization_id:
            errors["vendor"] = _("Vendor does not belong to this organization.")
        if self.warehouse_id and self.organization_id and self.warehouse.organization_id != self.organization_id:
            errors["warehouse"] = _("Warehouse does not belong to this organization.")
        if errors:
            raise ValidationError(errors)

    def __str__(self) -> str:
        if self.organization_id:
            return f"Purchase {self.purchase_number} from {self.vendor} ({self.organization.name})"
        return f"Purchase {self.purchase_number} from {self.vendor}"

    @property
    def subtotal(self) -> Decimal:
        """Sum of ordered line values before global discount and other costs."""
        return Decimal(str(sum((item.ordered_value for item in self.items.all()), Decimal("0.00")))).quantize(
            Decimal("0.01")
        )

    @property
    def total_value(self):
        """Value of goods actually received, not merely ordered."""
        return sum(item.received_value for item in self.items.all()) + self.other_costs - self.discount

    @property
    def ordered_value(self):
        """Value of goods ordered, before receiving."""
        return sum(item.ordered_value for item in self.items.all()) + self.other_costs - self.discount

    @property
    def balance_due(self) -> Decimal:
        """Remaining payable balance on this purchase order."""
        active_invoice = None
        if hasattr(self, "purchase_invoices"):
            active_invoice = next(
                (inv for inv in self.purchase_invoices.all() if not inv.is_voided),
                None,
            )
        if active_invoice is not None:
            payable = Decimal(str(active_invoice.total))
        elif any(item.quantity_received > 0 for item in self.items.all()):
            payable = Decimal(str(self.total_value))
        else:
            payable = Decimal(str(self.ordered_value))
        paid = Decimal(str(self.amount_paid or 0))
        return max(Decimal("0.00"), (payable - paid).quantize(Decimal("0.01")))

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

    objects = PurchaseItemQuerySet.as_manager()

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
    variant = auto_prefetch.ForeignKey(
        "products.ProductVariant",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="purchase_items",
        help_text=_("Specific variant of the product being ordered, if applicable."),
    )
    unit = auto_prefetch.ForeignKey(
        "products.ProductUnitConversion",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="purchase_items",
        help_text=_("Specific unit of measurement the product is being ordered in."),
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
    discount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        help_text=_("Discount applied to this line item."),
    )

    class Meta(auto_prefetch.Model.Meta):
        verbose_name = _("Purchase Item")
        verbose_name_plural = _("Purchase Items")
        ordering = ["created_at"]

    def clean(self) -> None:
        super().clean()
        errors = {}
        if self.purchase_id and self.product_id and self.product.organization_id != self.purchase.organization_id:
            errors["product"] = _("Product does not belong to the purchase order organization.")
        if self.variant_id and self.product_id and self.variant.product_id != self.product_id:
            errors["variant"] = _("Variant does not belong to the selected product.")
        if self.unit_id and self.product_id and self.unit.product_id != self.product_id:
            errors["unit"] = _("Unit conversion does not belong to the selected product.")
        if errors:
            raise ValidationError(errors)

    def __str__(self) -> str:
        return f"{self.product} (x{self.quantity_ordered})"

    @property
    def received_value(self):
        prorated_discount = (
            (self.discount / self.quantity_ordered) * self.quantity_received
            if self.quantity_ordered
            else 0
        )
        return max(0, (self.quantity_received * self.purchase_cost) - prorated_discount)

    @property
    def ordered_value(self):
        return max(0, (self.quantity_ordered * self.purchase_cost) - self.discount)


class PurchaseReturn(TimeBasedModel):
    """
    Records a return of damaged, defective, or excess goods back to the supplier (UI Page 7 Returns tab).
    """

    objects = PurchaseReturnQuerySet.as_manager()

    organization = auto_prefetch.ForeignKey(
        "users.Organization",
        on_delete=models.CASCADE,
        related_name="purchase_returns",
        help_text=_("Organization this supplier return belongs to."),
    )
    purchase = auto_prefetch.ForeignKey(
        "purchase.Purchase",
        on_delete=models.CASCADE,
        related_name="returns",
        help_text=_("Purchase order this return is recorded against."),
    )
    return_number = models.CharField(
        max_length=30,
        unique=True,
        editable=False,
        db_index=True,
        help_text=_("Auto-generated sequential reference, e.g. 'PRET-2026-0001'."),
    )
    returned_by = auto_prefetch.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="purchase_returns",
        help_text=_("User who recorded the supplier return."),
    )
    reason = models.TextField(
        blank=True,
        default="",
        help_text=_("Reason for returning goods to the supplier."),
    )
    total_refund_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        help_text=_("Total credit/refund value of the returned goods."),
    )

    class Meta(auto_prefetch.Model.Meta):
        verbose_name = _("Purchase Return")
        verbose_name_plural = _("Purchase Returns")
        ordering = ["-created_at"]

    def clean(self) -> None:
        super().clean()
        if self.purchase_id and self.organization_id and self.purchase.organization_id != self.organization_id:
            raise ValidationError({"purchase": _("Purchase order does not belong to this organization.")})

    def __str__(self) -> str:
        if self.organization_id:
            return f"Return {self.return_number} for {self.purchase.purchase_number} ({self.organization.name})"
        return f"Return {self.return_number} for {self.purchase.purchase_number}"

    def save(self, *args, **kwargs):
        if not self.return_number:
            year = date.today().year
            last = (
                PurchaseReturn.objects.filter(return_number__startswith=f"PRET-{year}-")
                .order_by("-return_number")
                .first()
            )
            last_seq = int(last.return_number.split("-")[-1]) if last else 0
            self.return_number = f"PRET-{year}-{str(last_seq + 1).zfill(4)}"
        super().save(*args, **kwargs)


class PurchaseReturnItem(TimeBasedModel):
    """
    A single line item returned to a supplier on a PurchaseReturn.
    """

    objects = PurchaseReturnItemQuerySet.as_manager()

    purchase_return = auto_prefetch.ForeignKey(
        "purchase.PurchaseReturn",
        on_delete=models.CASCADE,
        related_name="items",
    )
    purchase_item = auto_prefetch.ForeignKey(
        "purchase.PurchaseItem",
        on_delete=models.PROTECT,
        related_name="return_items",
    )
    batch = auto_prefetch.ForeignKey(
        "products.ProductBatch",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="purchase_return_items",
    )
    quantity = models.PositiveIntegerField(
        help_text=_("Quantity returned in the purchase item's ordered unit."),
    )
    unit_cost = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text=_("Unit cost credited for the returned item."),
    )
    line_total = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
    )

    class Meta(auto_prefetch.Model.Meta):
        verbose_name = _("Purchase Return Item")
        verbose_name_plural = _("Purchase Return Items")
        ordering = ["created_at"]

    def clean(self) -> None:
        super().clean()
        if (
            self.purchase_return_id
            and self.purchase_item_id
            and self.purchase_item.purchase_id != self.purchase_return.purchase_id
        ):
            raise ValidationError(
                {"purchase_item": _("Purchase item does not belong to this purchase order.")}
            )

    def __str__(self) -> str:
        return f"{self.purchase_item.product} Returned (x{self.quantity})"
