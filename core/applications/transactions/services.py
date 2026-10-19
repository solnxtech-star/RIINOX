from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from typing import Optional

from django.contrib.contenttypes.models import ContentType
from django.db import transaction

from core.applications.subscriptions.services.entitlements import (
    ensure_subscription_active,
)
from core.applications.subscriptions.services.usage import consume_usage
from core.applications.transactions.models import Transaction
from core.helper.enums import UsageMetric


@dataclass
class TransactionData:
    transaction_id: str
    organization: Any
    transaction_type: str
    status: str
    content_type: ContentType
    object_id: int
    created_by: Optional[Any] = None
    notes: Optional[str] = None


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
        object_id=data.object_id,
        created_by=data.created_by,
        notes=data.notes,
    )

    # Last step: reserve one unit of the allowance. At the limit this raises PLAN_LIMIT_REACHED,
    # and since we're inside atomic(), the insert above is undone too.
    consume_usage(data.organization, UsageMetric.TRANSACTIONS)
    return created
