from decimal import Decimal
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
    UnitOfMeasureChoices,
)
from core.applications.sales.queryset import (
    SaleQuerySet,
    SaleItemQuerySet,
    SaleReturnQuerySet,
    SaleReturnItemQuerySet,
)

class Sale(TimeBasedModel):
    objects = SaleQuerySet.as_manager()

    sale_id = models.CharField(max_length=50, unique=True, db_index=True)
    organization = auto_prefetch.ForeignKey(
        "users.Organization", on_delete=models.CASCADE, related_name="sales"
    )
    customer = auto_prefetch.ForeignKey(
        "customers.Customer", on_delete=models.PROTECT, related_name="sales"
    )
    warehouse = auto_prefetch.ForeignKey(
        "warehouse.Warehouse",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="sales",
        help_text=_("Warehouse the goods are supplied from."),
    )
    location = auto_prefetch.ForeignKey(
        "warehouse.StockLocation",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sales",
        help_text=_("Optional specific shelf/bin/dock inside the warehouse."),
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
    currency = models.CharField(max_length=3, default='NGN')
    reference_number = models.CharField(max_length=100, blank=True, null=True)
    due_date = models.DateField(blank=True, null=True)
    billing_address = models.TextField(blank=True, null=True)
    shipping_address = models.TextField(blank=True, null=True)

    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    # Snapshot of the organization's setting when the sale was made: True means
    # `tax_amount` is contained in the prices (total = subtotal - discount),
    # False means it was added on top (total = subtotal - discount + tax).
    prices_include_tax = models.BooleanField(default=False)
    total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    notes = models.TextField(blank=True, null=True)

    # Cancellation tracking (PRD §44)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = auto_prefetch.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cancelled_sales",
    )
    cancel_reason = models.TextField(blank=True, null=True)

    class Meta(auto_prefetch.Model.Meta):
        verbose_name = _("Sale")
        verbose_name_plural = _("Sales")
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["organization", "-created_at"], name="sale_org_created_idx"),
        ]
        constraints = [
        models.CheckConstraint(
            condition=models.Q(total__gte=0),
            name="sale_total_non_negative",
        ),
        models.CheckConstraint(
            condition=(
                models.Q(
                    cancelled_at__isnull=True,
                    cancelled_by__isnull=True,
                )
                | models.Q(
                    cancelled_at__isnull=False,
                    cancelled_by__isnull=False,
                )
            ),
            name="sale_cancelled_iff_has_actor",
        ),
    ]

    def __str__(self):
        return f"Sale {self.sale_id}"

class SaleItem(TimeBasedModel):
    objects = SaleItemQuerySet.as_manager()

    sale = auto_prefetch.ForeignKey(Sale, on_delete=models.CASCADE, related_name="items")
    product = auto_prefetch.ForeignKey("products.Product", on_delete=models.PROTECT, related_name="sale_items")
    variant = auto_prefetch.ForeignKey("products.ProductVariant", on_delete=models.SET_NULL, null=True, blank=True)

    # Historical immutability
    product_name = models.CharField(max_length=255)
    product_sku = models.CharField(max_length=100, blank=True, null=True)

    quantity = models.PositiveIntegerField(default=1)
    returned_quantity = models.PositiveIntegerField(default=0)

    unit_name = models.CharField(
        max_length=10,
        choices=UnitOfMeasureChoices.choices,
        default=UnitOfMeasureChoices.PIECE
    )
    unit_multiplier = models.DecimalField(max_digits=10, decimal_places=3, default=1.0)

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
        constraints = [
            models.CheckConstraint(
                condition=models.Q(returned_quantity__lte=models.F("quantity")),
                name="sale_item_returned_lte_quantity",
            ),
        ]

    @property
    def total(self) -> Decimal:
        if self.unit_price is None or self.quantity is None:
            return Decimal("0.00")
        discount = self.discount or Decimal("0.00")
        tax = self.tax or Decimal("0.00")
        net = (self.quantity * self.unit_price) - discount
        # With tax-inclusive prices the tax is already inside the price.
        return net if self.sale.prices_include_tax else net + tax

class SaleReturn(TimeBasedModel):
    objects = SaleReturnQuerySet.as_manager()

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
    objects = SaleReturnItemQuerySet.as_manager()

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


# --------------------------------------------------------------------------- #
# Cart (server-side point-of-sale basket)
# --------------------------------------------------------------------------- #
class CartStatus(models.TextChoices):
    ACTIVE = "active", _("Active")
    HELD = "held", _("Held")
    CHECKED_OUT = "checked_out", _("Checked out")
    ABANDONED = "abandoned", _("Abandoned")


OPEN_CART_STATUSES = (CartStatus.ACTIVE, CartStatus.HELD)


class Cart(TimeBasedModel):
    """
    A sale being built at a location, saved on the server so it survives a
    refresh, can be held and resumed, and becomes a Sale at checkout.

    A cart stores *what* is being sold, never prices: every read and the
    checkout price it with the same code as a sale, so the total shown is the
    total charged. Stock is checked, not reserved; it leaves only at checkout.
    Each member has at most one active cart per location (their till); any
    number may be held.
    """

    organization = auto_prefetch.ForeignKey(
        "users.Organization", on_delete=models.CASCADE, related_name="carts"
    )
    created_by = auto_prefetch.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="carts"
    )
    warehouse = auto_prefetch.ForeignKey(
        "warehouse.Warehouse",
        on_delete=models.PROTECT,
        related_name="carts",
        help_text=_("Location the goods will be sold from."),
    )
    customer = auto_prefetch.ForeignKey(
        "customers.Customer",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="carts",
    )
    status = models.CharField(max_length=20, choices=CartStatus, default=CartStatus.ACTIVE)
    label = models.CharField(
        max_length=60, blank=True, default="", help_text=_("Name for a held cart, e.g. 'Table 4'.")
    )
    notes = models.TextField(blank=True, default="")
    discount_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text=_("Discount on the whole cart. Not allowed together with line discounts."),
    )
    sale = models.OneToOneField(
        Sale, on_delete=models.PROTECT, null=True, blank=True, related_name="cart"
    )
    held_at = models.DateTimeField(null=True, blank=True)
    checked_out_at = models.DateTimeField(null=True, blank=True)
    last_activity_at = models.DateTimeField(
        auto_now_add=True, help_text=_("Last change. Idle open carts expire after CART_EXPIRY_DAYS.")
    )
    version = models.PositiveIntegerField(
        default=1, help_text=_("Goes up on every change, so a client can tell its copy is stale.")
    )

    class Meta(auto_prefetch.Model.Meta):
        verbose_name = _("Cart")
        verbose_name_plural = _("Carts")
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "created_by", "warehouse"],
                condition=models.Q(status="active"),
                name="cart_one_active_per_member_location",
            ),
            models.CheckConstraint(
                condition=models.Q(discount_amount__gte=0), name="cart_discount_not_negative"
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "created_by", "status"], name="cart_owner_status_idx"),
            models.Index(fields=["status", "last_activity_at"], name="cart_status_activity_idx"),
        ]

    def __str__(self) -> str:
        return f"Cart {self.pk} ({self.status})"


class CartItem(TimeBasedModel):
    """One product (variant, unit) in a cart. Adding the same line again adds to its quantity."""

    cart = auto_prefetch.ForeignKey(Cart, on_delete=models.CASCADE, related_name="items")
    product = auto_prefetch.ForeignKey(
        "products.Product", on_delete=models.CASCADE, related_name="cart_items"
    )
    variant = auto_prefetch.ForeignKey(
        "products.ProductVariant", on_delete=models.CASCADE, null=True, blank=True, related_name="cart_items"
    )
    unit_name = models.CharField(max_length=30)
    quantity = models.PositiveIntegerField()
    discount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    unit_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text=_("Price override (needs EDIT_SELLING_PRICE). Empty = the product's current price."),
    )

    class Meta(auto_prefetch.Model.Meta):
        verbose_name = _("Cart Item")
        verbose_name_plural = _("Cart Items")
        ordering = ["created_at"]
        constraints = [
            models.CheckConstraint(condition=models.Q(quantity__gte=1), name="cart_item_quantity_positive"),
            models.CheckConstraint(condition=models.Q(discount__gte=0), name="cart_item_discount_not_negative"),
        ]

    def __str__(self) -> str:
        return f"{self.quantity} x {self.product_id}"
