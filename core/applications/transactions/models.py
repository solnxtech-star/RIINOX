import auto_prefetch
from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils.translation import gettext_lazy as _

from core.helper.enums import (
    AccountNameChoices,
    TransactionStatusChoices,
    TransactionTypeChoices,
)
from core.helper.models import TimeBasedModel


class Transaction(TimeBasedModel):
    """
    The unified business event model. This represents a single atomic 
    operation (like a Sale, a Purchase, or a Stock Adjustment).
    It acts as the parent object for both Financial and Inventory effects.
    """
    transaction_id = models.CharField(
        _("Transaction ID"),
        max_length=50,
        unique=True,
        db_index=True,
        help_text=_("Unique human-readable identifier (e.g., TRX-2026-0001)."),
    )
    organization = auto_prefetch.ForeignKey(
        "users.Organization",
        on_delete=models.CASCADE,
        related_name="transactions",
    )
    transaction_type = models.CharField(
        max_length=30,
        choices=TransactionTypeChoices.choices,
        db_index=True,
    )
    status = models.CharField(
        max_length=20,
        choices=TransactionStatusChoices.choices,
        default=TransactionStatusChoices.PENDING,
        db_index=True,
    )
    
    # Generic relation to the originating document (Invoice, Payment, Adjustment)
    content_type = models.ForeignKey(ContentType, on_delete=models.SET_NULL, null=True, blank=True)
    object_id = models.PositiveIntegerField(null=True, blank=True)
    reference = GenericForeignKey("content_type", "object_id")

    created_by = auto_prefetch.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_transactions",
    )
    notes = models.TextField(blank=True, null=True)

    class Meta(auto_prefetch.Model.Meta):
        verbose_name = _("Transaction")
        verbose_name_plural = _("Transactions")
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.transaction_id} ({self.get_transaction_type_display()})"


class FinancialLedgerEntry(TimeBasedModel):
    """
    The financial effect of a Transaction, adhering to Double-Entry accounting.
    Every Transaction must have a set of Ledger Entries where total Debits = total Credits.
    """
    transaction = auto_prefetch.ForeignKey(
        Transaction,
        on_delete=models.CASCADE,
        related_name="financial_entries",
    )
    account_name = models.CharField(
        max_length=50,
        choices=AccountNameChoices.choices,
        db_index=True,
    )
    debit = models.DecimalField(
        max_digits=15, 
        decimal_places=2, 
        default=0,
        help_text=_("Amount debited to the account. Increases Assets/Expenses, decreases Liabilities/Revenue/Equity.")
    )
    credit = models.DecimalField(
        max_digits=15, 
        decimal_places=2, 
        default=0,
        help_text=_("Amount credited to the account. Increases Liabilities/Revenue/Equity, decreases Assets/Expenses.")
    )

    class Meta(auto_prefetch.Model.Meta):
        verbose_name = _("Financial Ledger Entry")
        verbose_name_plural = _("Financial Ledger Entries")

    def __str__(self):
        return f"{self.transaction.transaction_id} - {self.get_account_name_display()}: Dr {self.debit} | Cr {self.credit}"



