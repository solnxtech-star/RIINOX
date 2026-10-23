from __future__ import annotations

from decimal import Decimal
from typing import Final

from django.db.models import DecimalField
from django.db.models import ExpressionWrapper
from django.db.models import F
from django.db.models import Sum
from django.db.models.functions import Coalesce

from core.applications.customers.models import Customer
from core.applications.invoice.models import Invoice
from core.applications.invoice.queryset import OUTSTANDING_STATUSES

CENT: Final = Decimal("0.01")
MONEY: Final = DecimalField(max_digits=14, decimal_places=2)


def recalculate_customer_balance(customer: Customer) -> Decimal:
    """
    The one place Customer.balance is computed and written (PRD §20: derived
    from history). Balance is total - amount_paid over the customer's
    Confirmed, Overdue and Partially Paid invoices, scoped to the customer's
    organization. Idempotent.

    Call with the customer row locked, after the invoice change it reflects
    has been saved. Updates the passed instance as well as the row, and returns
    the new balance.

    Both the invoice ports adapter and the payment service call this function,
    so what is owed can never be computed two different ways.
    """
    owed = Invoice.objects.filter(
        organization_id=customer.organization_id,
        customer_id=customer.pk,
        status__in=OUTSTANDING_STATUSES,
    ).aggregate(
        owed=Coalesce(
            Sum(ExpressionWrapper(F("total") - F("amount_paid"), output_field=MONEY)),
            Decimal("0.00"),
            output_field=MONEY,
        )
    )["owed"].quantize(CENT)

    Customer.objects.filter(
        pk=customer.pk,
        organization_id=customer.organization_id,
    ).update(balance=owed)
    customer.balance = owed
    return owed
