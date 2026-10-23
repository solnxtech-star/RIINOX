from __future__ import annotations

from typing import TYPE_CHECKING, Any

from core.applications.notification.audit.action import AuditAction
from core.applications.notification.audit.services import record as audit_record

if TYPE_CHECKING:
    from core.applications.sales.models import Sale, SaleReturn


def record_sale_audit_event(
    sale: Sale,
    *,
    actor: Any = None,
    **extra: Any,
) -> None:
    """Record an audit log entry for sale creation."""
    audit_record(
        organization=sale.organization,
        actor=actor or sale.sales_rep,
        action=AuditAction.SALES_ORDER_CREATED,
        resource=sale,
        metadata={
            "sale_id": sale.sale_id,
            "total": str(sale.total),
            "payment_status": sale.payment_status,
            **extra,
        },
    )


def record_sale_cancellation_audit_event(
    sale: Sale,
    *,
    actor: Any = None,
    reason: str = "",
    **extra: Any,
) -> None:
    """Record an audit log entry for sale cancellation."""
    audit_record(
        organization=sale.organization,
        actor=actor or sale.cancelled_by or sale.sales_rep,
        action=AuditAction.SALES_ORDER_CANCELLED,
        resource=sale,
        metadata={
            "sale_id": sale.sale_id,
            "cancel_reason": reason,
            **extra,
        },
    )


def record_sale_return_audit_event(
    sale_return: SaleReturn,
    *,
    actor: Any = None,
    **extra: Any,
) -> None:
    """Record an audit log entry for sale return creation."""
    audit_record(
        organization=sale_return.organization,
        actor=actor or sale_return.original_sale.sales_rep,
        action=AuditAction.SALES_RETURN_CREATED,
        resource=sale_return,
        metadata={
            "sale_id": sale_return.original_sale.sale_id,
            "refund_amount": str(sale_return.refund_amount),
            "return_reason": sale_return.return_reason,
            **extra,
        },
    )
