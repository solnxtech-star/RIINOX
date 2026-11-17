"""
Expense workflows. Every write is atomic: the expense, its ledger transaction,
its double-entry lines and its audit event commit together or not at all.

Amount, location and payment method are fixed once recorded, because the
ledger already holds them. To correct one, void the expense (which posts the
reversing entries) and record a new one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils import timezone

from core.applications.expenses.errors import ExpenseAlreadyVoid
from core.applications.expenses.errors import ExpenseDateInFuture
from core.applications.expenses.errors import ExpenseNotEditable
from core.applications.expenses.models import Expense
from core.applications.expenses.models import ExpenseSequence
from core.applications.notification.audit.action import AuditAction
from core.applications.notification.audit.services import record as audit_record
from core.applications.subscriptions.services.entitlements import ensure_subscription_active
from core.applications.transactions.services import TransactionData
from core.applications.transactions.services import create_transaction
from core.applications.transactions.services import record_expense_financial_entries
from core.helper.enums import ExpenseStatus
from core.helper.enums import TransactionStatusChoices
from core.helper.enums import TransactionTypeChoices
from core.helper.money import quantize_money
from core.helper.org_time import organization_today

DEFAULT_CURRENCY = "NGN"
EDITABLE_FIELDS = ("category", "title", "expense_date", "paid_to", "reference", "notes")


@dataclass(frozen=True)
class ExpenseInput:
    location: Any
    category: str
    title: str
    amount: Decimal
    payment_method: str
    expense_date: date | None = None
    paid_to: str = ""
    reference: str = ""
    notes: str = ""


def _next_expense_number(organization) -> str:
    """Gap-free per-organization numbering; the row lock serializes concurrent writers."""
    ExpenseSequence.objects.get_or_create(organization=organization)
    sequence = ExpenseSequence.objects.select_for_update().get(organization=organization)
    sequence.last_number += 1
    sequence.save(update_fields=["last_number"])
    return f"EXP-{sequence.last_number:06d}"


def _check_date(organization, expense_date: date) -> None:
    if expense_date > organization_today(organization):
        raise ExpenseDateInFuture(detail={"expense_date": expense_date.isoformat()})


def _ledger_transaction(expense: Expense, *, actor, reverse: bool, notes: str | None = None):
    suffix = "-VOID" if reverse else ""
    record = create_transaction(
        TransactionData(
            transaction_id=f"EXP-{str(expense.pk).replace('-', '')[:24]}{suffix}"[:50],
            organization=expense.organization,
            transaction_type=TransactionTypeChoices.EXPENSE,
            status=TransactionStatusChoices.COMPLETED,
            content_type=ContentType.objects.get_for_model(Expense),
            object_id=expense.pk,
            created_by=actor,
            notes=notes,
            location=expense.location_id,
        )
    )
    record_expense_financial_entries(expense, record, reverse=reverse)
    return record


@transaction.atomic
def record_expense(*, organization, actor, data: ExpenseInput, audit_context=None) -> Expense:
    ensure_subscription_active(organization)
    expense_date = data.expense_date or organization_today(organization)
    _check_date(organization, expense_date)

    expense = Expense.objects.create(
        organization=organization,
        location=data.location,
        expense_number=_next_expense_number(organization),
        category=data.category,
        title=data.title.strip(),
        amount=quantize_money(data.amount, field="amount"),
        currency=(getattr(organization, "currency", "") or DEFAULT_CURRENCY).upper(),
        expense_date=expense_date,
        payment_method=data.payment_method,
        paid_to=(data.paid_to or "").strip(),
        reference=(data.reference or "").strip(),
        notes=data.notes or "",
        recorded_by=actor,
    )
    _ledger_transaction(expense, actor=actor, reverse=False)
    audit_record(
        action=AuditAction.EXPENSE_RECORDED,
        organization=organization,
        actor=actor,
        resource=expense,
        new_values={
            "amount": str(expense.amount),
            "category": expense.category,
            "title": expense.title,
            "location_id": str(expense.location_id),
        },
        context=audit_context,
    )
    return expense


@transaction.atomic
def update_expense(expense: Expense, *, actor, changes: dict[str, Any], audit_context=None) -> Expense:
    """Edit descriptive fields only (see module docstring)."""
    expense = Expense.objects.select_for_update().get(pk=expense.pk)
    if expense.status == ExpenseStatus.VOID:
        raise ExpenseNotEditable(detail={"expense_id": str(expense.pk)})

    changes = {field: value for field, value in changes.items() if field in EDITABLE_FIELDS}
    if "expense_date" in changes:
        _check_date(expense.organization, changes["expense_date"])

    previous = {field: _plain(getattr(expense, field)) for field in changes}
    for field, value in changes.items():
        setattr(expense, field, value.strip() if isinstance(value, str) and field != "notes" else value)
    if changes:
        expense.save(update_fields=[*changes, "updated_at"])
        audit_record(
            action=AuditAction.EXPENSE_UPDATED,
            organization=expense.organization,
            actor=actor,
            resource=expense,
            previous_values=previous,
            new_values={field: _plain(getattr(expense, field)) for field in changes},
            context=audit_context,
        )
    return expense


@transaction.atomic
def void_expense(expense: Expense, *, actor, reason: str, audit_context=None) -> Expense:
    """Mark void and post the reversing ledger entries. Safe against double voids."""
    expense = Expense.objects.select_for_update().get(pk=expense.pk)
    if expense.status == ExpenseStatus.VOID:
        raise ExpenseAlreadyVoid(detail={"expense_id": str(expense.pk), "voided_at": str(expense.voided_at)})

    expense.status = ExpenseStatus.VOID
    expense.voided_by = actor
    expense.voided_at = timezone.now()
    expense.void_reason = reason.strip()
    expense.save(update_fields=["status", "voided_by", "voided_at", "void_reason", "updated_at"])

    _ledger_transaction(expense, actor=actor, reverse=True, notes=expense.void_reason)
    audit_record(
        action=AuditAction.EXPENSE_VOIDED,
        organization=expense.organization,
        actor=actor,
        resource=expense,
        reason=expense.void_reason,
        metadata={"amount": str(expense.amount), "location_id": str(expense.location_id)},
        context=audit_context,
    )
    return expense


def _plain(value):
    return value.isoformat() if isinstance(value, date) else value
