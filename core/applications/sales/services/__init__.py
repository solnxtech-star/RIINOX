from __future__ import annotations

from core.applications.sales.services.audit import (
    record_sale_audit_event,
    record_sale_cancellation_audit_event,
    record_sale_return_audit_event,
)
from core.applications.sales.services.exceptions import (
    SaleCancellationError,
    SaleError,
    SaleNotFoundError,
    SaleValidationError,
)
from core.applications.sales.services.returns import (
    _returnable_amount,
    create_sale_return,
)
from core.applications.sales.services.sales import (
    _build_lines,
    _money,
    _reference,
    _unit_multiplier,
    cancel_sale,
    create_sale,
)

mock_create_audit_event = record_sale_audit_event

__all__ = [
    "create_sale",
    "cancel_sale",
    "create_sale_return",
    "_build_lines",
    "_money",
    "_reference",
    "_unit_multiplier",
    "_returnable_amount",
    "record_sale_audit_event",
    "record_sale_cancellation_audit_event",
    "record_sale_return_audit_event",
    "mock_create_audit_event",
    "SaleError",
    "SaleValidationError",
    "SaleNotFoundError",
    "SaleCancellationError",
]
