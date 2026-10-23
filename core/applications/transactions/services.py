from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Optional

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import transaction

from core.applications.subscriptions.services.entitlements import (
    ensure_subscription_active,
)
from core.applications.subscriptions.services.usage import consume_usage
from core.applications.transactions.models import FinancialLedgerEntry, Transaction
from core.helper.enums import AccountNameChoices, UsageMetric
from core.helper.money import quantize_money


@dataclass
class TransactionData:
    transaction_id: str
    organization: Any
    transaction_type: str
    status: str
    content_type: ContentType
    object_id: Any
    created_by: Optional[Any] = None
    notes: Optional[str] = None


@dataclass(frozen=True)
class FinancialEntryData:
    account_name: AccountNameChoices | str
    debit: Decimal = Decimal("0.00")
    credit: Decimal = Decimal("0.00")


@transaction.atomic
def create_transaction(data: TransactionData) -> Transaction:
    """
    Centralized service method to create a unified business event Transaction.
    Enforces active subscription standing and the monthly transaction allowance server-side.
    """
    ensure_subscription_active(data.organization)  # quick early refusal if the plan has lapsed

    created = Transaction.objects.create(
        transaction_id=data.transaction_id,
        organization=data.organization,
        transaction_type=data.transaction_type,
        status=data.status,
        content_type=data.content_type,
        object_id=str(data.object_id) if data.object_id is not None else None,
        created_by=data.created_by,
        notes=data.notes,
    )

    # Last step: reserve one unit of the allowance. At the limit this raises PLAN_LIMIT_REACHED,
    # and since we're inside atomic(), the insert above is undone too.
    consume_usage(data.organization, UsageMetric.TRANSACTIONS)
    return created


@transaction.atomic
def create_financial_entries(
    transaction_record: Transaction,
    entries: list[FinancialEntryData],
) -> list[FinancialLedgerEntry]:
    """
    Validates and writes double-entry financial ledger lines for a Transaction.

    Invariants:
    1. Sum of all debits MUST strictly equal the sum of all credits (tolerance < 0.01).
    2. Debits and credits must be non-negative and quantized to 2 decimal places.
    3. Bulk creates FinancialLedgerEntry records linked to transaction_record.

    Raises:
        ValidationError: If total debits != total credits or invalid amounts are passed.
    """
    if not entries:
        return []

    valid_entries: list[FinancialLedgerEntry] = []
    total_debits = Decimal("0.00")
    total_credits = Decimal("0.00")

    for entry in entries:
        debit = quantize_money(entry.debit, field="debit")
        credit = quantize_money(entry.credit, field="credit")

        if debit < Decimal("0.00") or credit < Decimal("0.00"):
            raise ValidationError("Financial ledger debit and credit amounts must be non-negative.")

        # Ignore lines where both debit and credit are zero
        if debit == Decimal("0.00") and credit == Decimal("0.00"):
            continue

        total_debits += debit
        total_credits += credit

        account_name_val = (
            entry.account_name.value
            if hasattr(entry.account_name, "value")
            else str(entry.account_name)
        )

        valid_entries.append(
            FinancialLedgerEntry(
                transaction=transaction_record,
                account_name=account_name_val,
                debit=debit,
                credit=credit,
            )
        )

    if not valid_entries:
        return []

    if abs(total_debits - total_credits) >= Decimal("0.01"):
        raise ValidationError(
            f"Double-entry bookkeeping unbalanced: total debits ({total_debits}) != total credits ({total_credits})."
        )

    return FinancialLedgerEntry.objects.bulk_create(valid_entries)


def record_sale_financial_entries(
    sale: Any,
    transaction_record: Transaction,
) -> list[FinancialLedgerEntry]:
    """
    Generates balanced double-entry ledger entries for a created Sale per PRD §42 and docs/transactions.rst.

    Accounting entries:
    - Debit:  AccountNameChoices.ACCOUNTS_RECEIVABLE (sale.total)
    - Debit:  AccountNameChoices.DISCOUNTS (sale.discount_amount, if > 0)
    - Credit: AccountNameChoices.SALES_REVENUE (sale.subtotal)
    - Credit: AccountNameChoices.TAX_PAYABLE (sale.tax_amount, if > 0)

    Inventory & COGS impact:
    - Debit:  AccountNameChoices.COGS (cogs_total)
    - Credit: AccountNameChoices.INVENTORY_ASSET (cogs_total)
    """
    entries: list[FinancialEntryData] = []

    # 1. Accounts Receivable for the net total payable
    total_amount = quantize_money(sale.total, field="sale.total")
    if total_amount > Decimal("0.00"):
        entries.append(
            FinancialEntryData(
                account_name=AccountNameChoices.ACCOUNTS_RECEIVABLE,
                debit=total_amount,
            )
        )

    # 2. Discounts given (contra-revenue / expense)
    discount_amount = quantize_money(
        getattr(sale, "discount_amount", 0) or 0, field="sale.discount_amount"
    )
    if discount_amount > Decimal("0.00"):
        entries.append(
            FinancialEntryData(
                account_name=AccountNameChoices.DISCOUNTS,
                debit=discount_amount,
            )
        )

    # 3. Sales Revenue (credit)
    subtotal = quantize_money(sale.subtotal, field="sale.subtotal")
    if subtotal > Decimal("0.00"):
        entries.append(
            FinancialEntryData(
                account_name=AccountNameChoices.SALES_REVENUE,
                credit=subtotal,
            )
        )

    # 4. Tax Payable (liability)
    tax_amount = quantize_money(
        getattr(sale, "tax_amount", 0) or 0, field="sale.tax_amount"
    )
    if tax_amount > Decimal("0.00"):
        entries.append(
            FinancialEntryData(
                account_name=AccountNameChoices.TAX_PAYABLE,
                credit=tax_amount,
            )
        )

    # 5. COGS & Inventory Asset (cost basis)
    cogs_total = Decimal("0.00")
    inv_entries = list(transaction_record.inventory_entries.all())
    if inv_entries:
        for ie in inv_entries:
            if ie.quantity_moved < 0 and ie.unit_cost:
                cogs_total += abs(Decimal(str(ie.quantity_moved))) * Decimal(str(ie.unit_cost))
    else:
        for item in sale.items.all():
            purchase_cost = getattr(item.product, "purchase_cost", None)
            if purchase_cost:
                qty = Decimal(str(item.quantity)) * Decimal(str(item.unit_multiplier))
                cogs_total += qty * Decimal(str(purchase_cost))

    cogs_total = quantize_money(cogs_total, field="cogs_total")
    if cogs_total > Decimal("0.00"):
        entries.append(
            FinancialEntryData(
                account_name=AccountNameChoices.COGS,
                debit=cogs_total,
            )
        )
        entries.append(
            FinancialEntryData(
                account_name=AccountNameChoices.INVENTORY_ASSET,
                credit=cogs_total,
            )
        )

    return create_financial_entries(transaction_record, entries)


def record_payment_financial_entries(
    payment: Any,
    transaction_record: Transaction,
) -> list[FinancialLedgerEntry]:
    """
    Generates balanced double-entry ledger entries when a payment is received.
    - Debit:  AccountNameChoices.CASH_BANK (payment.amount)
    - Credit: AccountNameChoices.ACCOUNTS_RECEIVABLE (payment.amount)
    """
    amount = quantize_money(payment.amount, field="payment amount")
    if amount <= Decimal("0.00"):
        return []

    entries = [
        FinancialEntryData(
            account_name=AccountNameChoices.CASH_BANK,
            debit=amount,
        ),
        FinancialEntryData(
            account_name=AccountNameChoices.ACCOUNTS_RECEIVABLE,
            credit=amount,
        ),
    ]
    return create_financial_entries(transaction_record, entries)


def record_sale_cancellation_financial_entries(
    sale: Any,
    transaction_record: Transaction,
) -> list[FinancialLedgerEntry]:
    """
    Reverses the double-entry accounting records of a cancelled sale per PRD §44.
    """
    entries: list[FinancialEntryData] = []

    total_amount = quantize_money(sale.total, field="sale.total")
    if total_amount > Decimal("0.00"):
        entries.append(
            FinancialEntryData(
                account_name=AccountNameChoices.ACCOUNTS_RECEIVABLE,
                credit=total_amount,
            )
        )

    discount_amount = quantize_money(
        getattr(sale, "discount_amount", 0) or 0, field="sale.discount_amount"
    )
    if discount_amount > Decimal("0.00"):
        entries.append(
            FinancialEntryData(
                account_name=AccountNameChoices.DISCOUNTS,
                credit=discount_amount,
            )
        )

    subtotal = quantize_money(sale.subtotal, field="sale.subtotal")
    if subtotal > Decimal("0.00"):
        entries.append(
            FinancialEntryData(
                account_name=AccountNameChoices.SALES_REVENUE,
                debit=subtotal,
            )
        )

    tax_amount = quantize_money(
        getattr(sale, "tax_amount", 0) or 0, field="sale.tax_amount"
    )
    if tax_amount > Decimal("0.00"):
        entries.append(
            FinancialEntryData(
                account_name=AccountNameChoices.TAX_PAYABLE,
                debit=tax_amount,
            )
        )

    cogs_total = Decimal("0.00")
    inv_entries = list(transaction_record.inventory_entries.all())
    if inv_entries:
        for ie in inv_entries:
            if ie.quantity_moved > 0 and ie.unit_cost:
                cogs_total += abs(Decimal(str(ie.quantity_moved))) * Decimal(str(ie.unit_cost))
    else:
        for item in sale.items.all():
            purchase_cost = getattr(item.product, "purchase_cost", None)
            if purchase_cost:
                qty = Decimal(str(item.quantity)) * Decimal(str(item.unit_multiplier))
                cogs_total += qty * Decimal(str(purchase_cost))

    cogs_total = quantize_money(cogs_total, field="cogs_total")
    if cogs_total > Decimal("0.00"):
        entries.append(
            FinancialEntryData(
                account_name=AccountNameChoices.INVENTORY_ASSET,
                debit=cogs_total,
            )
        )
        entries.append(
            FinancialEntryData(
                account_name=AccountNameChoices.COGS,
                credit=cogs_total,
            )
        )

    return create_financial_entries(transaction_record, entries)


def record_sale_return_financial_entries(
    sale_return: Any,
    transaction_record: Transaction,
) -> list[FinancialLedgerEntry]:
    """
    Generates balanced financial ledger entries for customer return and refund.
    - Debit:  AccountNameChoices.SALES_REVENUE (sale_return.refund_amount)
    - Credit: AccountNameChoices.ACCOUNTS_RECEIVABLE (sale_return.refund_amount)
    - Restocked Inventory:
      - Debit:  AccountNameChoices.INVENTORY_ASSET (restocked_cost)
      - Credit: AccountNameChoices.COGS (restocked_cost)
    """
    entries: list[FinancialEntryData] = []
    refund_amount = quantize_money(sale_return.refund_amount, field="refund_amount")

    if refund_amount > Decimal("0.00"):
        entries.append(
            FinancialEntryData(
                account_name=AccountNameChoices.SALES_REVENUE,
                debit=refund_amount,
            )
        )
        entries.append(
            FinancialEntryData(
                account_name=AccountNameChoices.ACCOUNTS_RECEIVABLE,
                credit=refund_amount,
            )
        )

    # Restocked items cost basis
    restocked_cost = Decimal("0.00")
    inv_entries = list(transaction_record.inventory_entries.all())
    if inv_entries:
        for ie in inv_entries:
            if ie.quantity_moved > 0 and ie.unit_cost:
                restocked_cost += abs(Decimal(str(ie.quantity_moved))) * Decimal(str(ie.unit_cost))
    else:
        for item in sale_return.items.all():
            purchase_cost = getattr(item.sale_item.product, "purchase_cost", None)
            if purchase_cost:
                qty = Decimal(str(item.quantity)) * Decimal(str(item.sale_item.unit_multiplier))
                restocked_cost += qty * Decimal(str(purchase_cost))

    restocked_cost = quantize_money(restocked_cost, field="restocked_cost")
    if restocked_cost > Decimal("0.00"):
        entries.append(
            FinancialEntryData(
                account_name=AccountNameChoices.INVENTORY_ASSET,
                debit=restocked_cost,
            )
        )
        entries.append(
            FinancialEntryData(
                account_name=AccountNameChoices.COGS,
                credit=restocked_cost,
            )
        )

    return create_financial_entries(transaction_record, entries)
