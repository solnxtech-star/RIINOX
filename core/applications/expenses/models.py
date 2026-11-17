"""
Operating expenses (rent, fuel, salaries, ...): money the business spends that is
not stock. Recorded per location, posted to the ledger, never deleted: a mistake
is voided, which posts the reversing entries (PRD §44).
"""

from __future__ import annotations

from decimal import Decimal

import auto_prefetch
from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from core.helper.enums import ExpenseCategory
from core.helper.enums import ExpenseStatus
from core.helper.enums import PaymentMethod
from core.helper.models import TimeBasedModel


class ExpenseQuerySet(auto_prefetch.QuerySet):
    def for_organization(self, organization):
        return self.filter(organization=organization)

    def recorded(self):
        """Expenses that count: everything except voided ones."""
        return self.filter(status=ExpenseStatus.RECORDED)

    def between(self, start, end):
        """`expense_date` in [start, end] (dates, inclusive)."""
        return self.filter(expense_date__gte=start, expense_date__lte=end)

    def with_details(self):
        return self.select_related("location", "recorded_by", "voided_by")


class Expense(TimeBasedModel):
    organization = auto_prefetch.ForeignKey(
        "users.Organization",
        on_delete=models.CASCADE,
        related_name="expenses",
    )
    location = auto_prefetch.ForeignKey(
        "warehouse.Warehouse",
        on_delete=models.PROTECT,
        related_name="expenses",
        help_text=_("Branch / location the money was spent for."),
    )
    expense_number = models.CharField(
        max_length=30,
        editable=False,
        help_text=_("Readable reference, unique per organization, e.g. EXP-000042."),
    )
    category = models.CharField(max_length=30, choices=ExpenseCategory.choices)
    title = models.CharField(max_length=150, help_text=_("Short description, e.g. 'Diesel for generator'."))
    amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    currency = models.CharField(max_length=3)
    expense_date = models.DateField(help_text=_("The day the money was spent (organization's calendar)."))
    payment_method = models.CharField(max_length=30, choices=PaymentMethod.choices)
    paid_to = models.CharField(max_length=150, blank=True, help_text=_("Payee, e.g. 'Total Filling Station'."))
    reference = models.CharField(max_length=100, blank=True, help_text=_("Receipt or transfer reference."))
    notes = models.TextField(blank=True)

    status = models.CharField(
        max_length=20,
        choices=ExpenseStatus.choices,
        default=ExpenseStatus.RECORDED,
        db_index=True,
    )
    recorded_by = auto_prefetch.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="recorded_expenses",
    )
    voided_by = auto_prefetch.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="voided_expenses",
    )
    voided_at = models.DateTimeField(null=True, blank=True)
    void_reason = models.TextField(blank=True)

    objects = auto_prefetch.Manager.from_queryset(ExpenseQuerySet)()

    class Meta(auto_prefetch.Model.Meta):
        verbose_name = _("Expense")
        verbose_name_plural = _("Expenses")
        ordering = ["-expense_date", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "expense_number"],
                name="unique_expense_number_per_org",
            ),
            models.CheckConstraint(condition=models.Q(amount__gt=0), name="expense_amount_positive"),
        ]
        indexes = [
            # List and dashboard totals: one organization, a date range, newest first.
            models.Index(fields=["organization", "-expense_date"], name="exp_org_date_idx"),
            models.Index(fields=["organization", "location", "expense_date"], name="exp_org_loc_date_idx"),
            models.Index(fields=["organization", "-created_at"], name="exp_org_created_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.expense_number} {self.title} ({self.amount} {self.currency})"


class ExpenseSequence(models.Model):
    """Per-organization counter behind `expense_number` (row-locked, gap-free)."""

    organization = models.OneToOneField(
        "users.Organization",
        on_delete=models.CASCADE,
        related_name="expense_sequence",
        primary_key=True,
    )
    last_number = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = _("Expense Number Sequence")
