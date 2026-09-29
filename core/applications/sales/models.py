import auto_prefetch
from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _
from core.helper.models import TimeBasedModel
from core.helper.enums import TransactionStatusChoices

class Sale(TimeBasedModel):
    sale_id = models.CharField(max_length=50, unique=True, db_index=True)
    organization = auto_prefetch.ForeignKey(
        "users.Organization", on_delete=models.CASCADE, related_name="sales"
    )
    customer = auto_prefetch.ForeignKey(
        "transactions.Customer", on_delete=models.PROTECT, related_name="sales"
    )
    location = auto_prefetch.ForeignKey(
        "warehouse.StockLocation", on_delete=models.PROTECT, related_name="sales"
    )
    sales_rep = auto_prefetch.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="sales"
    )
    status = models.CharField(
        max_length=20, choices=TransactionStatusChoices.choices, default=TransactionStatusChoices.PENDING
    )
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    notes = models.TextField(blank=True, null=True)

    class Meta(auto_prefetch.Model.Meta):
        verbose_name = _("Sale")
        verbose_name_plural = _("Sales")
        ordering = ["-created_at"]

    def __str__(self):
        return f"Sale {self.sale_id}"

class SaleItem(TimeBasedModel):
    sale = auto_prefetch.ForeignKey(Sale, on_delete=models.CASCADE, related_name="items")
    product = auto_prefetch.ForeignKey("products.Product", on_delete=models.PROTECT, related_name="sale_items")
    variant = auto_prefetch.ForeignKey("products.ProductVariant", on_delete=models.SET_NULL, null=True, blank=True)
    quantity = models.PositiveIntegerField(default=1)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    discount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    tax = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    class Meta(auto_prefetch.Model.Meta):
        verbose_name = _("Sale Item")
        verbose_name_plural = _("Sale Items")

    @property
    def total(self):
        return (self.quantity * self.unit_price) - self.discount + self.tax

class SaleReturn(TimeBasedModel):
    original_sale = auto_prefetch.ForeignKey(Sale, on_delete=models.CASCADE, related_name="returns")
    organization = auto_prefetch.ForeignKey("users.Organization", on_delete=models.CASCADE)
    return_reason = models.TextField()
    refund_amount = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(
        max_length=20, choices=TransactionStatusChoices.choices, default=TransactionStatusChoices.PENDING
    )

    class Meta(auto_prefetch.Model.Meta):
        verbose_name = _("Sale Return")
        verbose_name_plural = _("Sale Returns")


class SaleReturnItem(TimeBasedModel):
    sale_return = auto_prefetch.ForeignKey(SaleReturn, on_delete=models.CASCADE, related_name="items")
    sale_item = auto_prefetch.ForeignKey(SaleItem, on_delete=models.PROTECT, related_name="return_items")
    quantity = models.PositiveIntegerField(default=1)
    refund_amount = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta(auto_prefetch.Model.Meta):
        verbose_name = _("Sale Return Item")
        verbose_name_plural = _("Sale Return Items")
