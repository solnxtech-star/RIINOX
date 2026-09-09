import auto_prefetch
from django.conf import settings
from django.db import models
from django.utils.timezone import now
from django.utils.translation import gettext_lazy as _

from core.helper.enums import CustomerStatusChoices
from core.helper.enums import CustomerTypeChoices
from core.helper.enums import InvoiceStatusChoices
from core.helper.models import TimeBasedModel


class Client(TimeBasedModel):
    """
    Represents a client/customer who receives invoices from an organization
    (PRD §21).

    - Belongs to a single organization (multi-tenant isolation).
    - `current_balance` is a read cache, not editable directly — it's
      recomputed from `Invoice`/`InvoicePayment` totals by the service
      layer, same rationale as `Product.current_stock`.
    """

    organization = auto_prefetch.ForeignKey(
        "users.Organization",
        on_delete=models.CASCADE,
        related_name="clients",
    )
    name = models.CharField(_("Client Name"), max_length=255)
    email = models.EmailField(_("Client Email"), db_index=True)
    phone = models.CharField(_("Client Phone"), max_length=20, blank=True, null=True)
    address = models.TextField(_("Address"), blank=True, null=True)
    client_type = models.CharField(
        max_length=20,
        choices=CustomerTypeChoices.choices,
        default=CustomerTypeChoices.INDIVIDUAL,
    )
    credit_limit = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    current_balance = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        editable=False,
        help_text=_("Outstanding balance across all invoices. System-maintained."),
    )
    status = models.CharField(
        max_length=20,
        choices=CustomerStatusChoices.choices,
        default=CustomerStatusChoices.ACTIVE,
    )
    is_active = models.BooleanField(default=True)

    class Meta(auto_prefetch.Model.Meta):
        verbose_name = _("Client")
        verbose_name_plural = _("Clients")
        unique_together = ("organization", "email")
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.email})"


class Invoice(TimeBasedModel):
    """
    Represents an invoice issued to a client by an organization (PRD §22).

    `sales_rep` is who created it (typically Staff); `confirmed_by` is who
    confirmed/authorized the sale (typically Admin, or Staff under a
    business rule that allows self-confirmation) — kept as two distinct
    fields because the PRD's security model (§5, §14, §16) depends on
    Staff being the ones who create day-to-day invoices while sensitive
    actions stay attributable to whoever actually approved them.

    Business-specific fields can still be stored in `extra_data` as JSON.
    """

    client = auto_prefetch.ForeignKey(
        "invoice.Client",
        on_delete=models.CASCADE,
        related_name="invoices",
    )
    organization = auto_prefetch.ForeignKey(
        "users.Organization",
        on_delete=models.CASCADE,
        related_name="invoices",
    )
    warehouse = auto_prefetch.ForeignKey(
        "warehouse.Warehouse",
        on_delete=models.PROTECT,
        related_name="invoices",
        help_text=_("Warehouse stock is deducted from on confirmation."),
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
    invoice_number = models.CharField(max_length=50, unique=True, db_index=True)
    due_date = models.DateField(_("Due Date"))
    status = models.CharField(
        max_length=20,
        choices=InvoiceStatusChoices.choices,
        default=InvoiceStatusChoices.PENDING,
    )

    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        help_text=_("Frozen at confirm-time: subtotal - discount + tax."),
    )

    is_voided = models.BooleanField(default=False)
    voided_by = auto_prefetch.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="voided_invoices",
        limit_choices_to={"role": "admin"},
    )
    voided_at = models.DateTimeField(null=True, blank=True)
    void_reason = models.TextField(blank=True, null=True)
    replaces = auto_prefetch.ForeignKey(
        "invoice.Invoice",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="corrections",
        help_text=_("Set on the corrected invoice, pointing back at the voided original (PRD §14)."),
    )

    notes = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    extra_data = models.JSONField(default=dict, blank=True, null=True)

    class Meta(auto_prefetch.Model.Meta):
        verbose_name = _("Invoice")
        verbose_name_plural = _("Invoices")
        ordering = ["-created_at"]

    def __str__(self):
        return f"Invoice {self.invoice_number} for {self.client}"

    @property
    def computed_subtotal(self):
        """Live sum of line items — useful while the invoice is still a Draft."""
        return sum(item.total for item in self.items.all())


class InvoiceItem(TimeBasedModel):
    """
    Line item in an invoice, now tied to an actual Product (PRD §16, §39)
    so a sale can drive an InventoryTransaction deduction. `unit_price` is
    still snapshotted at sale time so later price changes on Product don't
    rewrite historical invoices.
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
    description = models.CharField(max_length=255, blank=True, null=True)
    quantity = models.PositiveIntegerField(default=1)
    unit_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text=_("Snapshot of Product.customer_sale_price at sale time."),
    )
    extra_data = models.JSONField(default=dict, blank=True, null=True)

    class Meta(auto_prefetch.Model.Meta):
        verbose_name = _("Invoice Item")
        verbose_name_plural = _("Invoice Items")

    @property
    def total(self):
        return self.quantity * self.unit_price

    def __str__(self):
        return f"{self.product} (x{self.quantity})"