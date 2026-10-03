import uuid
import auto_prefetch
from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from core.helper.enums import ProductStatusChoices
from core.helper.enums import ProductTypeChoices
from core.helper.enums import TaxConfigChoices
from core.helper.enums import UnitOfMeasureChoices
from core.helper.enums import UsersRole
from core.helper.media import MediaHelper
from core.helper.models import TimeBasedModel


class Category(TimeBasedModel):
    """Product category, scoped per organization."""

    organization = auto_prefetch.ForeignKey(
        "users.Organization",
        on_delete=models.CASCADE,
        related_name="product_categories",
        help_text=_("Organization this category belongs to."),
    )
    name = models.CharField(
        _("Category Name"),
        max_length=150,
        help_text=_("Display name of the category, e.g. 'Beverages'."),
    )
    description = models.TextField(
        blank=True,
        null=True,
        help_text=_("Optional longer description of what belongs in this category."),
    )
    is_active = models.BooleanField(
        default=True,
        help_text=_("Inactive categories are hidden from product-creation pickers."),
    )

    class Meta(auto_prefetch.Model.Meta):
        verbose_name = _("Category")
        verbose_name_plural = _("Categories")
        unique_together = ("organization", "name")
        ordering = ["name"]

    def __str__(self):
        return self.name


class Brand(TimeBasedModel):
    """Product brand, scoped per organization."""

    organization = auto_prefetch.ForeignKey(
        "users.Organization",
        on_delete=models.CASCADE,
        related_name="brands",
        help_text=_("Organization this brand belongs to."),
    )
    name = models.CharField(
        _("Brand Name"),
        max_length=150,
        help_text=_("Manufacturer or brand name, e.g. 'Dangote'."),
    )
    is_active = models.BooleanField(
        default=True,
        help_text=_("Inactive brands are hidden from product-creation pickers."),
    )

    class Meta(auto_prefetch.Model.Meta):
        verbose_name = _("Brand")
        verbose_name_plural = _("Brands")
        unique_together = ("organization", "name")
        ordering = ["name"]

    def __str__(self):
        return self.name


class Product(TimeBasedModel):
    """
    Core product/SKU record (PRD §6).

    Pricing split matters (PRD §7): `purchase_cost` and `vendor_sale_cost`
    are Admin-only fields and must never be exposed to Staff-facing
    serializers. `customer_sale_price` is the only price Staff may view.

    `current_stock` is intentionally NOT an editable field. It is a
    denormalized read cache maintained exclusively by
    `inventory.services.InventoryService` from the `InventoryTransaction`
    ledger, which is the actual source of truth (PRD §8, §9). Nothing —
    not even Admin — should write to it directly through a serializer;
    doing so is exactly the "silent stock change" the PRD forbids.
    """

    organization = auto_prefetch.ForeignKey(
        "users.Organization",
        on_delete=models.CASCADE,
        related_name="products",
        help_text=_("Organization this product belongs to."),
    )
    name = models.CharField(
        _("Product Name"),
        max_length=255,
        help_text=_("Customer-facing product name."),
    )
    sku = models.CharField(
        _("SKU"),
        max_length=100,
        db_index=True,
        help_text=_("Internal stock-keeping unit code, unique within the organization."),
    )
    barcode = models.CharField(
        _("Barcode / QR Code"),
        max_length=100,
        blank=True,
        null=True,
        db_index=True,
        help_text=_("Scannable barcode or QR payload used for quick lookup (PRD §35)."),
    )
    category = auto_prefetch.ForeignKey(
        "products.Category",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="products",
        help_text=_("Category this product is grouped under."),
    )
    brand = auto_prefetch.ForeignKey(
        "products.Brand",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="products",
        help_text=_("Manufacturer/brand of this product, if applicable."),
    )
    description = models.TextField(
        blank=True,
        null=True,
        help_text=_("Longer product description shown to staff and customers."),
    )
    quality_grade = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        help_text=_("Free-form product quality/grade label, e.g. 'Grade A'."),
    )
    unit_of_measurement = models.CharField(
        max_length=10,
        choices=UnitOfMeasureChoices.choices,
        default=UnitOfMeasureChoices.PIECE,
        help_text=_("Unit stock quantities for this product are counted in."),
    )
    product_type = models.CharField(
        max_length=20,
        choices=ProductTypeChoices.choices,
        default=ProductTypeChoices.PHYSICAL,
        help_text=_("Type of product (e.g., Physical, Service, Digital)."),
    )
    tax_config = models.CharField(
        max_length=20,
        choices=TaxConfigChoices.choices,
        default=TaxConfigChoices.TAXABLE,
        help_text=_("Tax configuration for this product."),
    )
    tax_rate = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        blank=True,
        null=True,
        help_text=_("Specific percentage tax rate override, if applicable."),
    )
    track_inventory = models.BooleanField(
        default=True,
        help_text=_("Whether to enforce inventory ledger tracking for this product."),
    )
    @property
    def primary_image_url(self):
        primary_image = self.images.filter(is_primary=True).first()
        if primary_image and primary_image.image:
            return primary_image.image.url
        return None
    # Admin-only pricing — never serialize these to Staff.
    purchase_cost = models.DecimalField(
        _("Purchase Cost"),
        max_digits=12,
        decimal_places=2,
        default=0,
        help_text=_("Admin-only. What the business pays the vendor per unit."),
    )
    vendor_sale_cost = models.DecimalField(
        _("Vendor Sale Cost"),
        max_digits=12,
        decimal_places=2,
        default=0,
        help_text=_("Admin-only. Cost basis used for vendor-facing terms."),
    )

    # Staff-visible price.
    customer_sale_price = models.DecimalField(
        _("Customer Sale Price"),
        max_digits=12,
        decimal_places=2,
        default=0,
        help_text=_("Price charged to customers. Visible to Staff but not editable by them."),
    )

    minimum_stock_level = models.PositiveIntegerField(
        default=0,
        help_text=_("Triggers LOW_STOCK notifications when on-hand quantity reaches this."),
    )
    opening_stock = models.PositiveIntegerField(
        default=0,
        help_text=_("Stock quantity recorded when the product was first created."),
    )
    @property
    def current_stock(self):
        """
        Dynamically computed from Inventory records to ensure 
        Inventory is the strict single source of truth.
        """
        return sum(inv.quantity for inv in self.inventory_records.all())


    status = models.CharField(
        max_length=20,
        choices=ProductStatusChoices.choices,
        default=ProductStatusChoices.ACTIVE,
        help_text=_("Lifecycle status. Archived/discontinued products stay visible in historical records."),
    )
    created_by = auto_prefetch.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_products",
        limit_choices_to=Q(
            memberships__role__in=[
                UsersRole.OWNER,
                UsersRole.ADMIN,
            ]
        ),
    )

    class Meta(auto_prefetch.Model.Meta):
        verbose_name = _("Product")
        verbose_name_plural = _("Products")
        unique_together = ("organization", "sku")
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.sku})"


class ProductImage(TimeBasedModel):
    product = auto_prefetch.ForeignKey(
        "products.Product",
        on_delete=models.CASCADE,
        related_name="images",
    )

    image = models.ImageField(
        upload_to=MediaHelper.get_image_upload_path,
    )

    is_primary = models.BooleanField(
        default=False,
        help_text=_("Whether this is the primary image for the product."),
    )

    sort_order = models.PositiveIntegerField( 
        default=0,
        help_text=_("Controls the display order of product images."),
    )

    class Meta(auto_prefetch.Model.Meta):
        verbose_name = _("Product Image")
        verbose_name_plural = _("Product Images")
        ordering = ["sort_order", "-created_at"]

    def __str__(self):
        return f"{self.product.name} - Image"


class ProductVariant(TimeBasedModel):
    """
    A specific variant of a product (e.g., "Size L, Color Red").
    """

    product = auto_prefetch.ForeignKey(
        "products.Product",
        on_delete=models.CASCADE,
        related_name="variants",
        help_text=_("Product this variant belongs to."),
    )
    name = models.CharField(
        _("Variant Name"),
        max_length=255,
        help_text=_("Name/description of this variant (e.g. 'Red / Large')."),
    )
    sku = models.CharField(
        _("Variant SKU"),
        max_length=100,
        blank=True,
        null=True,
        db_index=True,
        help_text=_("Optional specific SKU for this variant."),
    )
    barcode = models.CharField(
        _("Variant Barcode / QR Code"),
        max_length=100,
        blank=True,
        null=True,
        db_index=True,
        help_text=_("Optional specific barcode for this variant."),
    )

    # Optional price overrides
    purchase_cost = models.DecimalField(
        _("Purchase Cost Override"),
        max_digits=12,
        decimal_places=2,
        blank=True,
        null=True,
        help_text=_("Admin-only. Override what the business pays the vendor per unit."),
    )
    customer_sale_price = models.DecimalField(
        _("Customer Sale Price Override"),
        max_digits=12,
        decimal_places=2,
        blank=True,
        null=True,
        help_text=_("Override the price charged to customers for this specific variant."),
    )

    is_active = models.BooleanField(
        default=True,
        help_text=_("Whether this variant is active and available."),
    )

    class Meta(auto_prefetch.Model.Meta):
        verbose_name = _("Product Variant")
        verbose_name_plural = _("Product Variants")
        unique_together = ("product", "sku")
        ordering = ["product", "name"]

    def __str__(self):
        return f"{self.product.name} - {self.name}"


class ProductBulkDiscount(TimeBasedModel):
    """
    Bulk purchase discount rules for a specific product.
    e.g. Buy 4 or more, get a 40 NGN discount per item.
    """
    product = auto_prefetch.ForeignKey(
        Product, on_delete=models.CASCADE, related_name="bulk_discounts"
    )
    min_quantity = models.PositiveIntegerField(
        help_text=_("Minimum number of items required to trigger this discount.")
    )
    discount_amount = models.DecimalField(
        max_digits=12, decimal_places=2,
        help_text=_("Fixed discount amount applied.")
    )

    class Meta(auto_prefetch.Model.Meta):
        verbose_name = _("Product Bulk Discount")
        verbose_name_plural = _("Product Bulk Discounts")
        ordering = ["min_quantity"]

    def __str__(self):
        return f"Buy {self.min_quantity}+ get {self.discount_amount} off"


class ProductUnitConversion(TimeBasedModel):
    """
    Maps alternative units (e.g. Carton) to the base Unit of Measure (e.g. Pieces).
    """
    product = auto_prefetch.ForeignKey(
        Product, on_delete=models.CASCADE, related_name="unit_conversions"
    )
    unit_name = models.CharField(
        max_length=50,
        help_text=_("Name of the alternative unit, e.g. 'Carton'.")
    )
    multiplier = models.DecimalField(
        max_digits=10, decimal_places=3,
        help_text=_("Multiplier against the base unit. E.g. if base is Pieces and unit is Carton of 12, multiplier is 12.")
    )
    price_override = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
        help_text=_("Optional specific selling price for this unit, overriding the multiplier math.")
    )

    class Meta(auto_prefetch.Model.Meta):
        verbose_name = _("Product Unit Conversion")
        verbose_name_plural = _("Product Unit Conversions")
        unique_together = ("product", "unit_name")

    def __str__(self):
        return f"{self.unit_name} ({self.multiplier}x)"


class ProductBatch(TimeBasedModel):
    """
    Tracking batches for perishable or specifically priced goods.
    """
    product = auto_prefetch.ForeignKey(
        Product, on_delete=models.CASCADE, related_name="batches"
    )
    batch_number = models.CharField(
        max_length=100, db_index=True, blank=True,
        help_text=_("Identifier for the batch. Auto-generated if left blank.")
    )
    expiry_date = models.DateField(
        null=True, blank=True,
        help_text=_("Optional expiry date for this batch.")
    )
    cost_price = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
        help_text=_("Optional batch-specific cost price.")
    )
    selling_price = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
        help_text=_("Optional batch-specific selling price.")
    )

    class Meta(auto_prefetch.Model.Meta):
        verbose_name = _("Product Batch")
        verbose_name_plural = _("Product Batches")
        unique_together = ("product", "batch_number")

    def __str__(self):
        return f"{self.product.name} - Batch {self.batch_number}"

    def save(self, *args, **kwargs):
        if not self.batch_number:
            self.batch_number = f"BCH-{uuid.uuid4().hex[:6].upper()}"
        super().save(*args, **kwargs)

