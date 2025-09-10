from django.db import models
from django.utils.translation import gettext_lazy as _
from django.conf import settings

from core.helper.enums import InvoiceStatusChoices
from core.helper.models import TimeBasedModel
import auto_prefetch


class Invoice(TimeBasedModel):
    """
    Invoice issued to a client by an admin.
    """

    client = auto_prefetch.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="invoices",
        limit_choices_to={"role": "client"},
    )
    issued_by = auto_prefetch.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="issued_invoices",
        limit_choices_to={"role": "admin"},
    )
    invoice_number = models.CharField(max_length=50, unique=True, db_index=True)
    due_date = models.DateField()
    status = models.CharField(
        max_length=20, choices=InvoiceStatusChoices.choices, default=InvoiceStatusChoices.PENDING
    )
    notes = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Invoice")
        verbose_name_plural = _("Invoices")
        ordering = ["-created_at"]

    def __str__(self):
        return f"Invoice {self.invoice_number} for {self.client}"


class InvoiceItem(TimeBasedModel):
    """
    Line items in an invoice.
    """

    invoice = auto_prefetch.ForeignKey(
        Invoice, on_delete=models.CASCADE, related_name="items"
    )
    description = models.CharField(max_length=255)
    quantity = models.PositiveIntegerField(default=1)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    tax_rate = models.DecimalField(
        max_digits=5, decimal_places=2, default=0.00, help_text="Tax rate in %"
    )

    class Meta:
        verbose_name = _("Invoice Item")
        verbose_name_plural = _("Invoice Items")

    @property
    def total(self):
        return (self.unit_price * self.quantity) * (1 + self.tax_rate / 100)

    def __str__(self):
        return f"{self.description} (x{self.quantity})"
