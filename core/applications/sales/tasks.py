"""Sales background jobs, scheduled in settings.CELERY_BEAT_SCHEDULE."""

from __future__ import annotations

from celery import shared_task


@shared_task(name="sales.expire_idle_carts")
def expire_idle_carts() -> dict[str, int]:
    """Abandon idle open carts and delete long-abandoned ones (see services.carts)."""
    from core.applications.sales.services.carts import expire_idle_carts as run

    return run()
