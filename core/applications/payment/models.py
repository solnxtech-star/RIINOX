import auto_prefetch
from django.conf import settings
from django.db import models
from django.db import transaction
from django.utils.timezone import now
from django.utils.translation import gettext_lazy as _

from core.helper.enums import PaymentMethod
from core.helper.enums import PaymentStatus
from core.helper.media import MediaHelper
from core.helper.models import TimeBasedModel


class Payment(TimeBasedModel):
    """
    Core record of a financial transaction made by a user.
    Links to invoices via InvoicePayment.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payments",
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=10, default="NGN")
    method = models.CharField(
        max_length=30,
        choices=PaymentMethod.choices,
        db_index=True,
    )
    status = models.CharField(
        max_length=20,
        choices=PaymentStatus.choices,
        default=PaymentStatus.PENDING,
        db_index=True,
    )
    reference = models.CharField(
        _("Gateway Reference"),
        max_length=255,
        unique=True,
        help_text=_("Unique reference from the payment gateway"),
    )
    description = models.TextField(_("Description"), blank=True, null=True)

    class Meta(auto_prefetch.Model.Meta):
        verbose_name = _("Payment")
        verbose_name_plural = _("Payments")
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.method} {self.amount}{self.currency} ({self.status})"


class PaymentLog(TimeBasedModel):
    """
    Stores webhook events / gateway responses for audit.
    Keeps history of all notifications from external providers.
    """

    payment = models.ForeignKey(
        "Payment",
        on_delete=models.CASCADE,
        related_name="logs",
    )
    event = models.CharField(_("Event Type"), max_length=100)
    raw_data = models.JSONField(_("Raw Data"))
    processed = models.BooleanField(default=False)

    class Meta(auto_prefetch.Model.Meta):
        verbose_name = _("Payment Log")
        verbose_name_plural = _("Payment Logs")
        ordering = ["-created_at"]

    def __str__(self):
        return f"Log {self.event} for {self.payment.reference}"


class ReceiptSequenceCounter(TimeBasedModel):
    """
    Backs race-safe sequential receipt-number generation. One row per
    year; incremented under `select_for_update()` inside a transaction so
    two concurrent payments can never be assigned the same receipt number
    (the naive "query the max and add one" approach in the original model
    has exactly that race).
    """

    year = models.PositiveIntegerField(unique=True)
    last_sequence = models.PositiveIntegerField(default=0)

    class Meta(auto_prefetch.Model.Meta):
        verbose_name = _("Receipt Sequence Counter")
        verbose_name_plural = _("Receipt Sequence Counters")

    def __str__(self):
        return f"{self.year}: {self.last_sequence}"

    @classmethod
    def next_receipt_number(cls, year=None):
        year = year or now().year
        with transaction.atomic():
            counter, _created = cls.objects.select_for_update().get_or_create(year=year)
            counter.last_sequence += 1
            counter.save(update_fields=["last_sequence"])
            return f"R-{year}-{str(counter.last_sequence).zfill(4)}"


class InvoicePayment(TimeBasedModel):
    """
    Records a payment made towards an invoice. Functions as the Receipt
    entity from the PRD (§15, §23, §24) — sales_rep, balance_after, and
    the void trail are what the PRD requires that a bare payment record
    wouldn't capture; pdf_file backs the "generate as PDF / share by
    email or WhatsApp" requirement.
    """

    receipt_number = models.CharField(
        _("Receipt Number"),
        max_length=20,
        unique=True,
        editable=False,
        db_index=True,
    )
    invoice = auto_prefetch.ForeignKey(
        "invoice.Invoice",
        on_delete=models.CASCADE,
        related_name="payments",
    )
    payment = auto_prefetch.ForeignKey(
        "payment.Payment",
        on_delete=models.CASCADE,
        related_name="invoice_payments",
    )
    sales_rep = auto_prefetch.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="issued_receipts",
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    balance_after = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text=_("Remaining balance on the invoice immediately after this payment."),
    )
    pdf_file = models.FileField(
        upload_to=MediaHelper.get_image_upload_path,
        blank=True,
        null=True,
    )
    is_voided = models.BooleanField(default=False)
    voided_by = auto_prefetch.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="voided_receipts",
        limit_choices_to={"role": "admin"},
    )
    voided_at = models.DateTimeField(null=True, blank=True)
    received_on = models.DateTimeField(auto_now_add=True)

    class Meta(auto_prefetch.Model.Meta):
        verbose_name = _("Invoice Payment")
        verbose_name_plural = _("Invoice Payments")

    def __str__(self):
        return f"Receipt for Invoice {self.invoice.invoice_number} - {self.amount}"

    def save(self, *args, **kwargs):
        if not self.receipt_number:
            self.receipt_number = ReceiptSequenceCounter.next_receipt_number()
        super().save(*args, **kwargs)


class Refund(TimeBasedModel):
    """
    Record of a refund against a payment. `approved_by` ties refunds into
    the same Admin-approval accountability trail as stock adjustments
    (PRD §32).
    """

    payment = models.ForeignKey(
        "Payment",
        on_delete=models.CASCADE,
        related_name="refunds",
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    reason = models.CharField(max_length=255, blank=True, null=True)
    reference = models.CharField(
        _("Refund Reference"),
        max_length=255,
        unique=True,
        help_text=_("Unique reference from the payment gateway"),
    )
    status = models.CharField(
        max_length=20,
        choices=PaymentStatus.choices,
        default=PaymentStatus.PENDING,
    )
    approved_by = auto_prefetch.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_refunds",
        limit_choices_to={"role": "admin"},
    )
    approved_at = models.DateTimeField(null=True, blank=True)

    class Meta(auto_prefetch.Model.Meta):
        verbose_name = _("Refund")
        verbose_name_plural = _("Refunds")
        ordering = ["-created_at"]

    def __str__(self):
        return f"Refund {self.amount} for {self.payment.reference}"