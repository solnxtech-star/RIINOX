import auto_prefetch
from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _
from core.helper.models import TimeBasedModel
from core.helper.enums import (
    TransactionStatusChoices,
    SalePaymentStatusChoices,
    SaleFulfillmentStatusChoices,
    DiscountTypeChoices,
    RefundMethodChoices,
    ItemConditionChoices,
    RestockActionChoices,
)

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
    payment_status = models.CharField(
        max_length=20, choices=SalePaymentStatusChoices.choices, default=SalePaymentStatusChoices.UNPAID
    )
    fulfillment_status = models.CharField(
        max_length=20, choices=SaleFulfillmentStatusChoices.choices, default=SaleFulfillmentStatusChoices.UNFULFILLED
    )
    
    currency = models.CharField(max_length=3, default='USD')
    reference_number = models.CharField(max_length=100, blank=True, null=True)
    due_date = models.DateField(blank=True, null=True)
    billing_address = models.TextField(blank=True, null=True)
    shipping_address = models.TextField(blank=True, null=True)
    
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
    
    # Historical immutability
    product_name = models.CharField(max_length=255)
    product_sku = models.CharField(max_length=100, blank=True, null=True)
    
    quantity = models.PositiveIntegerField(default=1)
    returned_quantity = models.PositiveIntegerField(default=0)
    
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    discount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    discount_type = models.CharField(
        max_length=20, choices=DiscountTypeChoices.choices, default=DiscountTypeChoices.FIXED
    )
    tax = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)

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
    refund_method = models.CharField(
        max_length=20, choices=RefundMethodChoices.choices, default=RefundMethodChoices.ORIGINAL_PAYMENT
    )
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
    condition = models.CharField(
        max_length=20, choices=ItemConditionChoices.choices, default=ItemConditionChoices.NEW
    )
    restock_action = models.CharField(
        max_length=20, choices=RestockActionChoices.choices, default=RestockActionChoices.RETURN_TO_STOCK
    )

    class Meta(auto_prefetch.Model.Meta):
        verbose_name = _("Sale Return Item")
        verbose_name_plural = _("Sale Return Items")
