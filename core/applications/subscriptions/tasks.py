"""
Billing background jobs as Celery tasks, scheduled in settings.CELERY_BEAT_SCHEDULE.

The work lives in services/jobs.py (batched, one bad row never stops a batch).
Each task here only adds a non-overlap lock: if beat fires again while a run is
still going (slow provider, big backlog), the new run skips instead of racing the
old one. The services also lock rows, so this is a second line of defence that
also saves provider calls. Runs that hit a full batch simply continue on the next
schedule tick.
"""

from __future__ import annotations

import logging

from celery import shared_task
from django.core.cache import cache

from core.applications.subscriptions.services import jobs

logger = logging.getLogger("billing")

# Longer than any run can take (the task hard time limit is 5 minutes).
LOCK_TIMEOUT_SECONDS = 10 * 60


def _run_exclusive(name: str) -> dict[str, int]:
    lock_key = f"billing:job-lock:{name}"
    if not cache.add(lock_key, "1", timeout=LOCK_TIMEOUT_SECONDS):
        logger.info("billing.job_skipped_overlap job=%s", name)
        return {"skipped_overlap": 1}
    try:
        results = dict(jobs.JOBS[name]())
        logger.info("billing.job_done job=%s results=%s", name, results)
        return results
    finally:
        cache.delete(lock_key)


@shared_task(name="billing.run_renewals")
def run_renewals() -> dict[str, int]:
    return _run_exclusive("renewals")


@shared_task(name="billing.run_period_ends")
def run_period_ends() -> dict[str, int]:
    return _run_exclusive("period_ends")


@shared_task(name="billing.run_expirations")
def run_expirations() -> dict[str, int]:
    return _run_exclusive("expirations")


@shared_task(name="billing.run_reconciliation")
def run_reconciliation() -> dict[str, int]:
    return _run_exclusive("reconciliation")


@shared_task(name="billing.process_webhook_backlog")
def process_webhook_backlog() -> dict[str, int]:
    return _run_exclusive("webhooks")
