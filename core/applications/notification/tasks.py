"""Notification background work: email delivery and the daily expiry scan."""

from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task
from django.core.cache import cache
from django.db.models import Count

from core.applications.notification.email import send_templated_email

logger = logging.getLogger("notifications")
LOCK_SECONDS = 30 * 60


@shared_task(
    name="notifications.send_emails",
    autoretry_for=(Exception,),
    retry_backoff=30,
    retry_kwargs={"max_retries": 5},
    acks_late=True,
)
def send_notification_emails(emails: list[str], context: dict) -> int:
    """
    Used when NOTIFICATION_EMAIL_MODE = "celery". One email per recipient, so no
    address is exposed to the others; a failure raises so Celery retries the batch.
    """
    for address in emails:
        send_templated_email(to=address, template="notification/email/event", context=context)
    return len(emails)


@shared_task(name="notifications.scan_expiring_batches")
def scan_expiring_batches() -> dict[str, int]:
    """
    Daily: for every organization and branch with batches (with stock) expiring
    within that organization's `days_before_expiry`, send one Batch/Expiry alert.
    Grouped per branch, deduplicated per day; one bad organization never stops the run.
    """
    if not cache.add("notifications:expiry-scan-lock", "1", timeout=LOCK_SECONDS):
        return {"skipped_overlap": 1}
    try:
        return _scan_expiring_batches()
    finally:
        cache.delete("notifications:expiry-scan-lock")


def _scan_expiring_batches() -> dict[str, int]:
    from core.applications.inventory.models import Inventory
    from core.applications.notification.dispatch import deliver
    from core.applications.notification.events import BATCH_EXPIRY
    from core.applications.notification.preferences import setting_for
    from core.applications.users.models import Organization
    from core.applications.warehouse.models import Warehouse
    from core.helper.org_time import organization_today

    max_days = 365
    sent = failed = 0
    organization_ids = (
        Inventory.objects.filter(quantity__gt=0, batch__expiry_date__isnull=False)
        .values_list("product__organization", flat=True).distinct()
    )
    for organization in Organization.objects.filter(pk__in=organization_ids, is_active=True).iterator():
        try:
            setting = setting_for(organization, BATCH_EXPIRY)
            if not setting.enabled:
                continue
            today = organization_today(organization)
            days = min(int(setting.options.get("days_before_expiry", 30)), max_days)
            # One grouped query per organization: distinct products expiring, per branch.
            rows = (
                Inventory.objects.filter(
                    product__organization=organization, quantity__gt=0,
                    batch__expiry_date__gte=today, batch__expiry_date__lte=today + timedelta(days=days),
                )
                .values("warehouse")
                .annotate(products=Count("product", distinct=True))
            )
            locations = Warehouse.objects.in_bulk([row["warehouse"] for row in rows])
            for row in rows:
                products, location = row["products"], locations[row["warehouse"]]
                plural = "s are" if products != 1 else " is"
                deliver(
                    event=BATCH_EXPIRY, organization=organization,
                    title="Expiry Warning",
                    message=f"{products} product{plural} expiring within {days} days at {location.name}.",
                    location=location, reference=None,
                    link={"resource": "inventory", "query": {"location": str(location.pk)}},
                    data={"products": products, "days": days}, extra_users=(), exclude_user=None,
                    dedupe_key=f"{location.pk}:{today.isoformat()}", dedupe_hours=24,
                )
                sent += 1
        except Exception:  # noqa: BLE001 - next organization
            failed += 1
            logger.exception("notification.expiry_scan_failed", extra={"organization_id": str(organization.pk)})
    return {"branches_alerted": sent, "organizations_failed": failed}


OVERDUE_REMINDER_DAYS = 7
OVERDUE_MAX_PER_ORGANIZATION = 1000  # per run; the rest are picked up by the next run


@shared_task(name="notifications.scan_overdue_invoices")
def scan_overdue_invoices() -> dict[str, int]:
    """
    For every confirmed customer invoice past its due date with a balance, send
    an Overdue Payment alert to the invoice's branch; repeated weekly while unpaid
    (deduplicated per invoice). "Due" is judged on each organization's own calendar.
    """
    if not cache.add("notifications:overdue-scan-lock", "1", timeout=LOCK_SECONDS):
        return {"skipped_overlap": 1}
    try:
        return _scan_overdue_invoices()
    finally:
        cache.delete("notifications:overdue-scan-lock")


def _scan_overdue_invoices() -> dict[str, int]:
    from django.db.models import DecimalField
    from django.db.models import ExpressionWrapper
    from django.db.models import F

    from core.applications.invoice.models import Invoice
    from core.applications.invoice.queryset import OUTSTANDING_STATUSES
    from core.applications.notification.dispatch import deliver
    from core.applications.notification.events import OVERDUE_PAYMENT
    from core.applications.notification.preferences import setting_for
    from core.applications.notification.triggers import _money
    from core.applications.users.models import Organization
    from core.helper.org_time import organization_today

    alerted = failed = 0
    balance = ExpressionWrapper(F("total") - F("amount_paid"), output_field=DecimalField(max_digits=14, decimal_places=2))
    organization_ids = Invoice.objects.filter(status__in=OUTSTANDING_STATUSES).values_list(
        "organization", flat=True
    ).distinct()
    for organization in Organization.objects.filter(pk__in=organization_ids, is_active=True).iterator():
        try:
            if not setting_for(organization, OVERDUE_PAYMENT).enabled:
                continue
            today = organization_today(organization)
            invoices = (
                Invoice.objects.filter(organization=organization, status__in=OUTSTANDING_STATUSES, due_date__lt=today)
                .annotate(balance=balance)
                .filter(balance__gt=0)
                .select_related("customer", "warehouse")
                .order_by("due_date")[:OVERDUE_MAX_PER_ORGANIZATION]
            )
            for invoice in invoices:
                customer = getattr(invoice.customer, "name", "") or "A customer"
                days = (today - invoice.due_date).days
                sent = deliver(
                    event=OVERDUE_PAYMENT, organization=organization, title="Overdue Payment",
                    message=(f"{customer} owes {_money(invoice.balance, organization)} on {invoice.invoice_number} "
                             f"(due {invoice.due_date:%d %b}, {days} day{'s' if days != 1 else ''} overdue)."),
                    location=invoice.warehouse, reference=invoice,
                    link={"resource": "invoices", "query": {"id": str(invoice.pk)}},
                    data={"invoice_id": str(invoice.pk), "invoice_number": invoice.invoice_number,
                          "customer_name": customer, "balance": str(invoice.balance),
                          "due_date": invoice.due_date.isoformat(), "days_overdue": days},
                    extra_users=(), exclude_user=None,
                    dedupe_key=f"invoice:{invoice.pk}", dedupe_hours=OVERDUE_REMINDER_DAYS * 24,
                )
                alerted += 1 if sent else 0
        except Exception:  # noqa: BLE001 - next organization
            failed += 1
            logger.exception("notification.overdue_scan_failed", extra={"organization_id": str(organization.pk)})
    return {"invoices_alerted": alerted, "organizations_failed": failed}
