"""
Webhook pipeline. The endpoint verifies the signature, stores the event, and answers
immediately; processing is a separate, retryable step. A burst of deliveries therefore
costs one insert each, and a processing failure is recorded and retried from the table
instead of lost.

BILLING_WEBHOOK_PROCESS_INLINE (default True) processes right after commit, in the
request. Set it to False to leave events RECEIVED for the process_webhook_backlog job
(or a task queue) once volume warrants it.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime
from datetime import timedelta
from enum import StrEnum
from typing import Any

from django.conf import settings
from django.db import IntegrityError
from django.db import transaction
from django.db.models import F
from django.db.models import Q
from django.utils import timezone

from core.applications.subscriptions.models import WebhookEvent
from core.applications.subscriptions.providers.errors import (
    MalformedProviderPayloadError,
)
from core.applications.subscriptions.providers.registry import get_provider
from core.applications.subscriptions.services.payments import apply_payment_event
from core.helper.enums import WebhookEventStatus as Status

logger = logging.getLogger("billing")

MAX_ATTEMPTS = 5
BACKLOG_MIN_AGE = timedelta(seconds=30)  # give inline processing a head start before the job picks an event up


class ReceiptStatus(StrEnum):
    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"  # redelivery of an event we already hold
    IGNORED = "ignored"      # a verified event we deliberately don't act on


def receive_webhook(
    provider_name: str, *, body: bytes, headers: Mapping[str, str]
) -> ReceiptStatus:
    """
    Entry point for POST /webhooks/<provider>/. InvalidWebhookSignatureError propagates:
    the view answers 401 and nothing is stored. Everything else answers 200 so the provider
    stops retrying; failures are recorded on the stored event instead.
    """
    provider = get_provider(provider_name)
    envelope = provider.verify_webhook(body=body, headers=headers)  # raises InvalidWebhookSignatureError

    try:
        actionable = provider.parse_event(envelope.payload) is not None
    except MalformedProviderPayloadError as exc:
        _store(provider_name, envelope, Status.FAILED, attempts=MAX_ATTEMPTS, error=f"Malformed: {exc}")
        return ReceiptStatus.ACCEPTED
    if not actionable:
        return ReceiptStatus.IGNORED  # not stored: keeps the table to events that matter

    event = _store(provider_name, envelope, Status.RECEIVED)
    if event is None:
        return ReceiptStatus.DUPLICATE
    if getattr(settings, "BILLING_WEBHOOK_PROCESS_INLINE", True):
        transaction.on_commit(lambda: _process_safely(event.pk))
    return ReceiptStatus.ACCEPTED


def _store(provider_name: str, envelope: Any, status: str, *, attempts: int = 0, error: str = "") -> WebhookEvent | None:
    try:
        with transaction.atomic():
            return WebhookEvent.objects.create(
                provider=provider_name, event_id=envelope.event_id, event_type=envelope.event_type,
                payload=envelope.payload, status=status, attempts=attempts, last_error=error[:1000],
            )
    except IntegrityError:
        return None  # the unique (provider, event_id) key: we already hold this event


def _process_safely(event_id: Any) -> None:
    try:
        process_webhook_event(event_id)
    except Exception:  # the event row records the failure; the response to the provider must not
        logger.exception("billing.webhook_processing_failed event=%s", event_id)


def process_webhook_event(event_id: Any, *, now: datetime | None = None) -> None:
    """Settle one stored event. Safe to call repeatedly and concurrently."""
    now = now or timezone.now()
    with transaction.atomic():
        event = (
            WebhookEvent.objects.select_for_update(skip_locked=True)
            .filter(pk=event_id, status__in=(Status.RECEIVED, Status.FAILED), attempts__lt=MAX_ATTEMPTS)
            .first()
        )
        if event is None:
            return  # processed, exhausted, or another worker holds it

        try:
            with transaction.atomic():  # savepoint: a failed settlement rolls back without losing the event row
                payment_event = get_provider(event.provider).parse_event(event.payload)
                if payment_event is None:
                    event.status, event.transaction_id = Status.IGNORED, None
                else:
                    settlement = apply_payment_event(payment_event, now=now)
                    event.status = Status.PROCESSED
                    event.transaction_id = settlement.payment.pk if settlement.payment else None
            event.last_error = ""
            event.processed_at = now
        except MalformedProviderPayloadError as exc:
            event.status, event.attempts, event.last_error = Status.FAILED, MAX_ATTEMPTS, f"Malformed: {exc}"[:1000]
        except Exception as exc:
            event.status, event.last_error = Status.FAILED, f"{exc.__class__.__name__}: {exc}"[:1000]
            logger.exception("billing.webhook_settlement_failed event=%s", event.pk)
        event.attempts = F("attempts") + 1 if event.attempts < MAX_ATTEMPTS else event.attempts
        event.save()


def process_webhook_backlog(*, limit: int = 100, now: datetime | None = None) -> int:
    """Settle events still RECEIVED (inline processing off or died) and failed ones with attempts left."""
    now = now or timezone.now()
    ids = list(
        WebhookEvent.objects.filter(
            Q(status=Status.RECEIVED) | Q(status=Status.FAILED, attempts__lt=MAX_ATTEMPTS),
            created_at__lte=now - BACKLOG_MIN_AGE,
        )
        .order_by("created_at")
        .values_list("pk", flat=True)[:limit],
    )
    for event_id in ids:
        _process_safely(event_id)
    return len(ids)
