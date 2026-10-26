from typing import ClassVar

import auto_prefetch
from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from core.applications.invoice.queryset import DocumentSequenceManager
from core.applications.invoice.queryset import DocumentTemplateManager
from core.applications.invoice.queryset import InvoiceItemManager
from core.applications.invoice.queryset import InvoiceManager
from core.applications.invoice.queryset import PurchaseInvoiceItemManager
from core.applications.invoice.queryset import PurchaseInvoiceManager
from core.helper.enums import TEMPLATE_TYPES
from core.helper.enums import InvoiceStatusChoices
from core.helper.enums import UnitOfMeasureChoices
from core.helper.media import MediaHelper
from core.helper.models import TimeBasedModel
from core.helper.storage import RawCloudinaryStorage


class Invoice(TimeBasedModel):
    """
    The legal document for a sale (PRD §22, §24).

    An invoice has one of two origins:

    - Sale-origin: issued from a `sales.Sale` through
      `issue_invoice_for_sale`. The sale owns the order and the stock
      movement; the invoice owns the number, issue date and status. These
      invoices are born confirmed, never move stock themselves, and are
      voided through the sale.
    - Standalone: created as a Draft with `create_invoice` and confirmed with
      `confirm_invoice` (sale is NULL).

    `sales_rep` is who sold; `confirmed_by` is who authorized the issue, kept
    as two fields so sensitive actions stay attributable (PRD §5, §14, §16).

    `amount_paid` is a system-maintained cache written only by the invoice
    services and the payments service. The payment records stay the source of
    truth; `ports.has_payments` is the authoritative check before a void.

    Business-specific fields can still be stored in `extra_data` as JSON.
    """
    customer_name = models.CharField(max_length=255, blank=True, default="")
    customer_company_name = models.CharField(max_length=255, blank=True, default="")
    customer_tax_id = models.CharField(max_length=50, blank=True, default="")
    customer_email = models.EmailField(blank=True, default="")
    customer_phone = models.CharField(max_length=35, blank=True, default="")
    billing_address = models.TextField(blank=True, default="")
    shipping_address = models.TextField(blank=True, default="")
    idempotency_key = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        editable=False,
        help_text=_("Client-supplied key that makes a retried create request return the same invoice."),
    )
    customer = auto_prefetch.ForeignKey(
        "customers.Customer",
        on_delete=models.PROTECT,
        related_name="invoices",
    )
    organization = auto_prefetch.ForeignKey(
        "users.Organization",
        on_delete=models.CASCADE,
        related_name="invoices",
    )
    sale = auto_prefetch.ForeignKey(
        "sales.Sale",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="invoices",
        help_text=_(
            "The sale this invoice was issued for. NULL for standalone invoices."
        ),
    )
    warehouse = auto_prefetch.ForeignKey(
        "warehouse.Warehouse",
        on_delete=models.PROTECT,
        related_name="invoices",
        help_text=_("Warehouse the goods are supplied from."),
    )
    sales_rep = auto_prefetch.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="sales_invoices",
    )
    confirmed_by = auto_prefetch.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="confirmed_invoices",
    )
    invoice_number = models.CharField(max_length=50)
    due_date = models.DateField(_("Due Date"))
    status = models.CharField(
        max_length=20,
        choices=InvoiceStatusChoices.choices,
        default=InvoiceStatusChoices.DRAFT,
    )

    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        help_text=_("Frozen at issue/confirm time: subtotal - discount + tax."),
    )
    amount_paid = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        editable=False,
        help_text=_("Total received against this invoice. System-maintained."),
    )
    currency = models.CharField(
        max_length=10,
        blank=True,
        null=True,
        help_text=_("ISO 4217 code, frozen at issue time so later changes never rewrite a historical invoice."),
    )
    archived_at = models.DateTimeField(
        null=True,
        blank=True,
        editable=False,
        help_text=_("Set when a settled invoice is hidden from the default list. NULL otherwise."),
    )
    voided_by = auto_prefetch.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="voided_invoices",
    )
    voided_at = models.DateTimeField(null=True, blank=True)
    void_reason = models.TextField(blank=True, null=True)
    replaces = auto_prefetch.ForeignKey(
        "invoice.Invoice",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="corrections",
        help_text=_(
            "Set on the corrected invoice, pointing back at the voided original (PRD §24)."
        ),
    )
    issued_at = models.DateTimeField(
        null=True,
        blank=True,
        editable=False,
        help_text=_(
            "Set when the invoice is issued or confirmed. NULL for Draft and Pending."
        ),
    )
    notes = models.TextField(blank=True, null=True)
    extra_data = models.JSONField(default=dict, blank=True, null=True)

    objects = InvoiceManager()

    class Meta(auto_prefetch.Model.Meta):
        verbose_name = _("Invoice")
        verbose_name_plural = _("Invoices")
        ordering = ["-created_at"]
        indexes: ClassVar = [
            models.Index(fields=["organization", "-created_at"], name="inv_org_created_idx"),
            models.Index(
                fields=["organization", "status", "due_date"],
                name="inv_org_status_due_idx",
            ),
            models.Index(fields=["organization", "customer"], name="inv_org_customer_idx"),
            models.Index(fields=["organization", "issued_at"], name="inv_org_issued_idx"),
        ]
        constraints: ClassVar = [
            models.UniqueConstraint(
                fields=["organization", "invoice_number"],
                name="unique_invoice_number_per_org",
            ),
            models.UniqueConstraint(
                fields=["organization", "idempotency_key"],
                condition=models.Q(idempotency_key__isnull=False),
                name="unique_invoice_idempotency_key_per_org",
            ),
            # One live (non-cancelled) invoice per sale. A voided invoice frees
            # the sale for a new one.
            models.UniqueConstraint(
                fields=["sale"],
                condition=models.Q(sale__isnull=False)
                & ~models.Q(status=InvoiceStatusChoices.CANCELLED),
                name="unique_live_invoice_per_sale",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    subtotal__gte=0,
                    discount_amount__gte=0,
                    tax_amount__gte=0,
                    total__gte=0,
                    amount_paid__gte=0,
                ),
                name="invoice_amounts_non_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(discount_amount__lte=models.F("subtotal")),
                name="invoice_discount_within_subtotal",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    total=models.F("subtotal")
                    - models.F("discount_amount")
                    + models.F("tax_amount")
                ),
                name="invoice_total_matches_components",
            ),
            models.CheckConstraint(
                condition=models.Q(amount_paid__lte=models.F("total")),
                name="invoice_paid_within_total",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(status=InvoiceStatusChoices.CANCELLED, voided_at__isnull=False)
                    | (
                        ~models.Q(status=InvoiceStatusChoices.CANCELLED)
                        & models.Q(voided_at__isnull=True)
                    )
                ),
                name="invoice_cancelled_iff_voided",
            ),
        ]

    def __str__(self):
        return f"Invoice {self.invoice_number} for {self.customer_name}"

    @property
    def is_voided(self) -> bool:
        return self.status == InvoiceStatusChoices.CANCELLED

    @property
    def is_sale_origin(self) -> bool:
        return self.sale_id is not None

    @property
    def balance_due(self):
        return self.total - self.amount_paid

    @property
    def computed_subtotal(self):
        """
        Live sum of line amounts before discount and tax,
        useful while still a Draft.
        """
        return sum(item.line_subtotal for item in self.items.all())


class InvoiceItem(TimeBasedModel):
    """
    A line on an invoice (PRD §16, §39). Everything here is a snapshot taken
    when the line was created, so later changes to the product, its price or
    its unit conversions never rewrite a historical invoice.

    `quantity` is in the line's own unit (`unit_name`); `unit_multiplier` is
    how many base units one such unit represents.
    """

    invoice = auto_prefetch.ForeignKey(
        "invoice.Invoice",
        on_delete=models.CASCADE,
        related_name="items",
    )
    product = auto_prefetch.ForeignKey(
        "products.Product",
        on_delete=models.PROTECT,
        related_name="invoice_items",
    )
    variant = auto_prefetch.ForeignKey(
        "products.ProductVariant",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="invoice_items",
    )
    description = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text=_("Snapshot of the product name at sale time."),
    )
    quantity = models.PositiveIntegerField(default=1)
    unit_name = models.CharField(
        max_length=10,
        choices=UnitOfMeasureChoices.choices,
        default=UnitOfMeasureChoices.PIECE,
    )
    unit_multiplier = models.DecimalField(max_digits=10, decimal_places=3, default=1)
    unit_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text=_("Price per line unit at sale time."),
    )
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    extra_data = models.JSONField(default=dict, blank=True, null=True)

    objects = InvoiceItemManager()

    class Meta(auto_prefetch.Model.Meta):
        verbose_name = _("Invoice Item")
        verbose_name_plural = _("Invoice Items")
        constraints: ClassVar = [
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0),
                name="invoice_item_quantity_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(unit_multiplier__gt=0),
                name="invoice_item_multiplier_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(unit_price__gte=0, discount_amount__gte=0, tax_amount__gte=0),
                name="invoice_item_amounts_non_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(tax_rate__gte=0, tax_rate__lte=100),
                name="invoice_item_tax_rate_in_range",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    discount_amount__lte=models.F("quantity") * models.F("unit_price")
                ),
                name="invoice_item_discount_within_line",
            ),
        ]

    @property
    def line_subtotal(self):
        return self.quantity * self.unit_price

    @property
    def total(self):
        return self.line_subtotal - self.discount_amount + self.tax_amount

    def __str__(self):
        return f"{self.product} (x{self.quantity})"


class DocumentTemplate(TimeBasedModel):
    """
    Layout/style template for invoices, receipts, quotes, and credit notes.
    `organization` is null for shared Default/System templates and set for
    an organization's own Custom template — keeping custom templates
    correctly tenant-isolated instead of a single global pool.
    """

    name = models.CharField(max_length=100)
    description = models.TextField(blank=True, null=True)
    template_type = models.CharField(
        _("Template Type"),
        max_length=20,
        choices=TEMPLATE_TYPES.choices,
        default=TEMPLATE_TYPES.INVOICE,
    )
    file = models.FileField(
        storage=RawCloudinaryStorage(),
        upload_to=MediaHelper.get_template_upload_path,
        max_length=255,
        help_text=_("The actual template file (e.g. HTML, DOCX)."),
    )
    organization = auto_prefetch.ForeignKey(
        "users.Organization",
        on_delete=models.CASCADE,
        related_name="document_templates",
        null=True,
        blank=True,
        help_text=_(
            "Owning organization for a Custom template. Null for Default/System templates."
        ),
    )
    is_default = models.BooleanField(
        default=False,
        help_text=_(
            "The shared default for its type. Only valid when organization is null."
        ),
    )
    is_active = models.BooleanField(default=True)
    objects = DocumentTemplateManager()

    class Meta(auto_prefetch.Model.Meta):
        verbose_name = _("Document Template")
        verbose_name_plural = _("Document Templates")
        ordering = ["name"]
        constraints: ClassVar = [
            models.UniqueConstraint(
                fields=["organization", "name"],
                name="unique_template_name_per_org",
            ),
            models.UniqueConstraint(
                fields=["template_type"],
                condition=models.Q(organization__isnull=True, is_default=True),
                name="unique_default_template_per_type",
            ),
            models.CheckConstraint(
                condition=models.Q(is_default=False) | models.Q(organization__isnull=True),
                name="default_template_is_shared",
            ),
            models.UniqueConstraint(
                fields=["name"],
                condition=models.Q(organization__isnull=True),
                name="unique_shared_template_name",
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.template_type})"


class DocumentSequence(TimeBasedModel):
    """
    Per-organization, per-document-type numbering (PRD §7's "document
    numbering"), e.g. invoices as INV-000123, receipts as RCT-000045.
    `next_number` should only ever be advanced inside the same DB
    transaction that creates the document, using select_for_update, to
    avoid two documents racing for the same number.
    """

    organization = auto_prefetch.ForeignKey(
        "users.Organization",
        on_delete=models.CASCADE,
        related_name="document_sequences",
    )
    document_type = models.CharField(max_length=20, choices=TEMPLATE_TYPES.choices)
    prefix = models.CharField(max_length=20, blank=True)
    padding = models.PositiveIntegerField(default=6)
    next_number = models.PositiveIntegerField(default=1)
    objects = DocumentSequenceManager()

    class Meta(auto_prefetch.Model.Meta):
        verbose_name = _("Document Sequence")
        verbose_name_plural = _("Document Sequences")
        constraints: ClassVar = [
            models.UniqueConstraint(
                fields=["organization", "document_type"],
                name="unique_sequence_per_org_and_type",
            ),
        ]

    def __str__(self):
        return f"{self.organization.name} — {self.document_type} sequence"


class PurchaseInvoice(TimeBasedModel):
    """
    A supplier/purchase invoice recorded against a Purchase Order (PRD §18, UI Pages 1-3 & 7).
    Mirrors `Invoice` for procurement while snapshotting vendor details and billed line items.
    """

    organization = auto_prefetch.ForeignKey(
        "users.Organization",
        on_delete=models.CASCADE,
        related_name="purchase_invoices",
        help_text=_("Organization this supplier invoice belongs to."),
    )
    vendor = auto_prefetch.ForeignKey(
        "vendors.Vendor",
        on_delete=models.PROTECT,
        related_name="purchase_invoices",
        help_text=_("Supplier who issued this invoice."),
    )
    purchase = auto_prefetch.ForeignKey(
        "purchase.Purchase",
        on_delete=models.PROTECT,
        related_name="purchase_invoices",
        help_text=_("Purchase order this supplier invoice is linked to."),
    )
    warehouse = auto_prefetch.ForeignKey(
        "warehouse.Warehouse",
        on_delete=models.PROTECT,
        related_name="purchase_invoices",
        help_text=_("Destination warehouse for the purchase."),
    )
    recorded_by = auto_prefetch.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="recorded_purchase_invoices",
        help_text=_("User who recorded this supplier invoice."),
    )
    billed_to = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text=_("Recipient or entity billed on the supplier invoice (UI Page 1)."),
    )
    invoice_type = models.CharField(
        max_length=30,
        default="supplier_invoice",
        help_text=_("Classification of invoice, e.g. 'supplier_invoice' (UI Page 1)."),
    )
    vendor_company_name = models.CharField(max_length=255, blank=True, default="")
    vendor_contact_person = models.CharField(max_length=150, blank=True, default="")
    vendor_email = models.EmailField(blank=True, default="")
    vendor_phone = models.CharField(max_length=35, blank=True, default="")
    vendor_address = models.TextField(blank=True, default="")

    invoice_number = models.CharField(
        max_length=50,
        db_index=True,
        help_text=_("System-generated reference, e.g. 'PINV-2026-0001'."),
    )
    vendor_invoice_number = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text=_("Supplier's own external invoice/reference number."),
    )
    issue_date = models.DateField(
        null=True,
        blank=True,
        help_text=_("Date the supplier invoice was issued (UI Page 1)."),
    )
    due_date = models.DateField(
        null=True,
        blank=True,
        help_text=_("Date payment is due to the supplier (UI Page 1)."),
    )
    status = models.CharField(
        max_length=20,
        choices=InvoiceStatusChoices.choices,
        default=InvoiceStatusChoices.CONFIRMED,
    )
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    other_costs = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    amount_paid = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        editable=False,
        help_text=_("Total paid towards this supplier invoice."),
    )
    currency = models.CharField(max_length=10, default="NGN")
    supporting_document = models.FileField(
        upload_to=MediaHelper.get_image_upload_path,
        blank=True,
        null=True,
        help_text=_("Scanned supplier invoice or waybill attachment."),
    )
    notes = models.TextField(blank=True, null=True)
    voided_by = auto_prefetch.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="voided_purchase_invoices",
    )
    voided_at = models.DateTimeField(null=True, blank=True)
    void_reason = models.TextField(blank=True, null=True)

    objects = PurchaseInvoiceManager()

    class Meta(auto_prefetch.Model.Meta):
        verbose_name = _("Purchase Invoice")
        verbose_name_plural = _("Purchase Invoices")
        ordering = ["-created_at"]
        constraints: ClassVar = [
            models.UniqueConstraint(
                fields=["organization", "invoice_number"],
                name="unique_purchase_invoice_number_per_org",
            ),
            models.UniqueConstraint(
                fields=["purchase"],
                condition=~models.Q(status=InvoiceStatusChoices.CANCELLED),
                name="unique_live_purchase_invoice_per_purchase",
            ),
        ]

    def clean(self) -> None:
        from django.core.exceptions import ValidationError

        super().clean()
        errors = {}
        if self.vendor_id and self.organization_id and self.vendor.organization_id != self.organization_id:
            errors["vendor"] = _("Vendor does not belong to this organization.")
        if self.purchase_id and self.organization_id and self.purchase.organization_id != self.organization_id:
            errors["purchase"] = _("Purchase order does not belong to this organization.")
        if self.warehouse_id and self.organization_id and self.warehouse.organization_id != self.organization_id:
            errors["warehouse"] = _("Warehouse does not belong to this organization.")
        if errors:
            raise ValidationError(errors)

    def __str__(self) -> str:
        if self.organization_id:
            return f"PurchaseInvoice {self.invoice_number} for {self.vendor_company_name} ({self.organization.name})"
        return f"PurchaseInvoice {self.invoice_number} for {self.vendor_company_name}"

    @property
    def is_voided(self) -> bool:
        return self.status == InvoiceStatusChoices.CANCELLED

    @property
    def balance_due(self):
        from decimal import Decimal

        return max(Decimal("0.00"), self.total - self.amount_paid)


class PurchaseInvoiceItem(TimeBasedModel):
    """
    A line item on a PurchaseInvoice (UI Pages 2-3: Product, Variant, Unit, Ordered, Invoice Qty, Unit Cost, Total).
    """

    purchase_invoice = auto_prefetch.ForeignKey(
        "invoice.PurchaseInvoice",
        on_delete=models.CASCADE,
        related_name="items",
    )
    purchase_item = auto_prefetch.ForeignKey(
        "purchase.PurchaseItem",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="purchase_invoice_items",
    )
    product = auto_prefetch.ForeignKey(
        "products.Product",
        on_delete=models.PROTECT,
        related_name="purchase_invoice_items",
    )
    variant = auto_prefetch.ForeignKey(
        "products.ProductVariant",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="purchase_invoice_items",
    )
    description = models.CharField(max_length=255, blank=True, null=True)
    unit_name = models.CharField(max_length=50, blank=True, default="")
    unit_multiplier = models.DecimalField(max_digits=10, decimal_places=3, default=1)
    quantity_ordered = models.PositiveIntegerField(default=1)
    invoice_quantity = models.PositiveIntegerField(default=1)
    unit_cost = models.DecimalField(max_digits=12, decimal_places=2)
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    line_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    objects = PurchaseInvoiceItemManager()

    class Meta(auto_prefetch.Model.Meta):
        verbose_name = _("Purchase Invoice Item")
        verbose_name_plural = _("Purchase Invoice Items")
        ordering = ["created_at"]

    def clean(self) -> None:
        from django.core.exceptions import ValidationError

        super().clean()
        errors = {}
        if (
            self.purchase_invoice_id
            and self.product_id
            and self.product.organization_id != self.purchase_invoice.organization_id
        ):
            errors["product"] = _("Product does not belong to the purchase invoice organization.")
        if self.variant_id and self.product_id and self.variant.product_id != self.product_id:
            errors["variant"] = _("Variant does not belong to the selected product.")
        if errors:
            raise ValidationError(errors)

    def __str__(self) -> str:
        return f"{self.product} (x{self.invoice_quantity})"
