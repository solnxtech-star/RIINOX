from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Callable
from typing import Any

from core.applications.subscriptions.models import Subscription
from core.applications.subscriptions.services import lifecycle
from core.applications.subscriptions.services.reconciliation import abandon_stale
from core.applications.subscriptions.services.reconciliation import reconcile_pending
from core.applications.subscriptions.services.renewals import renew_subscription
from core.applications.subscriptions.services.webhooks import process_webhook_backlog

logger = logging.getLogger("billing")
BATCH_SIZE = 200


def _run(label: str, ids: list[Any], unit: Callable[[Any], Any]) -> Counter[str]:
    results: Counter[str] = Counter()
    for subscription_id in ids:
        try:
            outcome = unit(subscription_id)
            results[getattr(outcome, "value", "done" if outcome else "skipped")] += 1
        except Exception:  # one bad row must not stop the batch
            logger.exception("billing.job_item_failed job=%s subscription=%s", label, subscription_id)
            results["error"] += 1
    return results


def run_renewals(batch_size: int = BATCH_SIZE) -> Counter[str]:
    """Charge due renewals and due dunning retries."""
    ids = list(Subscription.objects.due_for_renewal().values_list("pk", flat=True)[:batch_size])
    ids += list(Subscription.objects.due_for_retry().values_list("pk", flat=True)[:batch_size])
    return _run("renewals", ids, renew_subscription)


def run_period_ends(batch_size: int = BATCH_SIZE) -> Counter[str]:
    """End paid periods that will not renew."""
    ids = list(Subscription.objects.due_for_period_end().values_list("pk", flat=True)[:batch_size])
    return _run("period_ends", ids, lifecycle.end_period)


def run_expirations(batch_size: int = BATCH_SIZE) -> Counter[str]:
    """Expire finished trials and past-due subscriptions out of grace."""
    results = _run(
        "trials", list(Subscription.objects.trials_expired().values_list("pk", flat=True)[:batch_size]),
        lifecycle.expire_trial,
    )
    results += _run(
        "grace", list(Subscription.objects.grace_expired().values_list("pk", flat=True)[:batch_size]),
        lifecycle.expire_past_due,
    )
    return results


def run_reconciliation() -> Counter[str]:
    """Verify pending payments with the provider, then abandon what is still pending after a day."""
    return Counter(reconciled=reconcile_pending(), abandoned=abandon_stale())


def run_webhook_backlog() -> Counter[str]:
    return Counter(processed=process_webhook_backlog())


JOBS: dict[str, Callable[[], Counter[str]]] = {
    "renewals": run_renewals,
    "period_ends": run_period_ends,
    "expirations": run_expirations,
    "reconciliation": run_reconciliation,
    "webhooks": run_webhook_backlog,
}
