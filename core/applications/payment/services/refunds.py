from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from django.db.models import Sum
from django.utils import timezone

from core.applications.payment.exceptions import PaymentValidationError
from core.applications.payment.models import InvoicePayment, Refund
from core.helper.enums import PaymentStatus
from core.helper.utils import generate_reference

if TYPE_CHECKING:
    from core.applications.sales.models import SaleReturn


_reference = generate_reference



def record_refund_for_return(sale_return: SaleReturn) -> Refund | None:
    """
    Record a Refund against a payment of the original sale's invoice (PRD §23,
    §26). The refund can never exceed what that payment has left after earlier
    refunds. Returns None when the sale was never paid (a credit sale): that
    return needs a credit note, not a cash refund.
    """
    refund_amount = sale_return.refund_amount
    receipts = (
        InvoicePayment.objects.filter(invoice__sale=sale_return.original_sale)
        .select_related("payment")
        .order_by("pk")
    )
    for receipt in receipts:
        payment = receipt.payment
        refunded = (
            Refund.objects.filter(payment=payment, status=PaymentStatus.SUCCESS).aggregate(
                total=Sum("amount")
            )["total"]
            or Decimal("0.00")
        )
        if payment.amount - refunded >= refund_amount:
            sales_rep = sale_return.original_sale.sales_rep
            return Refund.objects.create(
                payment=payment,
                amount=refund_amount,
                reason=(sale_return.return_reason or "Sale Return")[:255],
                reference=_reference("REF", str(sale_return.id)[:8]),
                status=PaymentStatus.SUCCESS,
                approved_by=sales_rep if getattr(sales_rep, "is_staff", False) else None,
                approved_at=timezone.now(),
            )
    if receipts:
        msg = "The refund exceeds what is left on the original payments."
        raise PaymentValidationError(
            msg,
            detail={
                "sale_return_id": str(sale_return.id),
                "refund_amount": str(refund_amount),
            },
        )
    return None
