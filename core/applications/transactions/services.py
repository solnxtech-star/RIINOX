from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils import timezone

from core.applications.subscriptions.services import (
    check_plan_limit,
    ensure_subscription_active,
    get_plan_limit,
    get_subscription,
)
from core.applications.transactions.models import Transaction


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


def ensure_transaction_limit(organization: Any) -> None:
    """
    Asserts that the organization has not exceeded its monthly transaction limit
    for the current billing period.
    """
    limit = get_plan_limit(organization, "max_transactions_per_month")
    if limit is None:
        return

    subscription = get_subscription(organization)
    if subscription and subscription.current_period_start:
        start_date = subscription.current_period_start
    else:
        start_date = timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    cycle_count = Transaction.objects.filter(
        organization=organization,
        created_at__gte=start_date,
    ).count()

    check_plan_limit(organization, "max_transactions_per_month", cycle_count)


@transaction.atomic
def create_transaction(data: TransactionData) -> Transaction:
    """
    Centralized service method to create a unified business event Transaction.
    Enforces active subscription standing and max_transactions_per_month limit server-side.
    """
    ensure_subscription_active(data.organization)
    ensure_transaction_limit(data.organization)

    return Transaction.objects.create(
        transaction_id=data.transaction_id,
        organization=data.organization,
        transaction_type=data.transaction_type,
        status=data.status,
        content_type=data.content_type,
        object_id=data.object_id,
        created_by=data.created_by,
        notes=data.notes,
    )
