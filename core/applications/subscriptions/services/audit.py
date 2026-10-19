"""Subscription history (PRD §31): every state change is one append-only event."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from typing import Any

from django.db import transaction

from core.applications.subscriptions.models import SubscriptionEvent
from core.applications.subscriptions.services.entitlements import invalidate_on_commit
from core.applications.subscriptions.signals import subscription_changed

if TYPE_CHECKING:
    from core.applications.subscriptions.models import PaymentTransaction
    from core.applications.subscriptions.models import Plan
    from core.applications.subscriptions.models import Subscription
    from core.applications.users.models import User


@dataclass(frozen=True, slots=True)
class AuditContext:
    """Who did it and from where. Build it from the request in the API layer."""

    actor: User | None = None
    ip_address: str | None = None
    user_agent: str = ""
    reason: str = ""


SYSTEM = AuditContext()  # background jobs and webhooks


def record_event(
    subscription: Subscription,
    event_type: str,
    *,
    from_status: str = "",
    from_plan: Plan | None = None,
    to_plan: Plan | None = None,
    payment: PaymentTransaction | None = None,
    context: AuditContext = SYSTEM,
    metadata: dict[str, Any] | None = None,
) -> SubscriptionEvent:
    """
    Append one event, drop the organization's cached entitlements, and notify
    receivers (notifications, central audit log) after commit. Call it inside the
    same transaction as the change it describes.
    """
    event = SubscriptionEvent.objects.create(
        organization_id=subscription.organization_id,
        subscription=subscription,
        event_type=event_type,
        from_status=from_status,
        to_status=subscription.status,
        from_plan=from_plan,
        to_plan=to_plan,
        transaction=payment,
        actor=context.actor,
        reason=context.reason[:255],
        ip_address=context.ip_address,
        user_agent=context.user_agent[:255],
        metadata=metadata or {},
    )
    invalidate_on_commit(subscription.organization_id)
    transaction.on_commit(lambda: subscription_changed.send(sender=SubscriptionEvent, event=event))
    return event
