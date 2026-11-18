"""
How notification emails leave the server (settings.NOTIFICATION_EMAIL_MODE):

- "thread" (default): sent straight from the web process on a small background
  thread pool, over the configured email backend (Brevo's HTTP API in production).
  No Celery worker needed, and the request that caused the alert (e.g. a sale)
  doesn't wait for the mail provider. Trade-off: an email in flight when the
  process restarts is lost (the in-app notification is not).
- "celery": queued to a Celery worker, which retries failures with backoff.
  Use once a worker runs.
- "sync": sent inline before returning (tests, scripts).

Whatever the mode, a failed email never breaks the business action: it is logged.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from django.conf import settings

from core.applications.notification.email import send_templated_email

logger = logging.getLogger("notifications")

TEMPLATE = "notification/email/event"
MODES = ("thread", "celery", "sync")

# Small and bounded: email is I/O-bound, and this caps concurrent provider calls.
_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="notification-mail")


def send_now(emails: list[str], context: dict) -> int:
    """One email per recipient (no address exposed to others); one failure doesn't stop the rest."""
    sent = 0
    for address in emails:
        try:
            send_templated_email(to=address, template=TEMPLATE, context=context)
            sent += 1
        except Exception:  # noqa: BLE001 - keep going with the other recipients
            logger.exception("notification.email_failed", extra={"title": context.get("title")})
    return sent


def send_notification_emails(emails: list[str], context: dict) -> None:
    mode = getattr(settings, "NOTIFICATION_EMAIL_MODE", "thread")
    if mode not in MODES:
        logger.error("notification.bad_email_mode", extra={"mode": mode})
        mode = "thread"

    if mode == "sync":
        send_now(emails, context)
    elif mode == "celery":
        from core.applications.notification.tasks import send_notification_emails as task

        try:
            task.delay(emails, context)
        except Exception:  # noqa: BLE001 - broker down: send directly rather than lose it
            logger.exception("notification.email_enqueue_failed")
            _pool.submit(send_now, emails, context)
    else:
        _pool.submit(send_now, emails, context)
