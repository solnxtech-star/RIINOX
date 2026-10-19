"""Pure time and money helpers: no database access, trivially unit-testable."""

from __future__ import annotations

import calendar
import uuid
from datetime import datetime
from datetime import timedelta

from core.helper.enums import BillingInterval

_MONTHS_PER_INTERVAL = {BillingInterval.MONTHLY: 1, BillingInterval.YEARLY: 12}
_ONE_MICROSECOND = timedelta(microseconds=1)


def add_interval(moment: datetime, interval: str) -> datetime:
    """
    `moment` plus one billing interval, calendar-aware. The day clamps to the end
    of a shorter month (Jan 31 + 1 month = Feb 28/29), so a period that starts on
    the 29th-31st settles on the 28th from then on.
    """
    months = _MONTHS_PER_INTERVAL[BillingInterval(interval)]
    index = moment.month - 1 + months
    year = moment.year + index // 12
    month = index % 12 + 1
    day = min(moment.day, calendar.monthrange(year, month)[1])
    return moment.replace(year=year, month=month, day=day)


def prorated_credit_minor(*, amount_minor: int, period_start: datetime, period_end: datetime, now: datetime) -> int:
    """
    Unused value of a paid period, in minor units, rounded down. Integer math
    only, so there is no float drift on money.
    """
    total = (period_end - period_start) // _ONE_MICROSECOND
    remaining = (period_end - max(now, period_start)) // _ONE_MICROSECOND
    if total <= 0 or remaining <= 0:
        return 0
    return amount_minor * min(remaining, total) // total


def compact_id(pk: object) -> str:
    return str(pk).replace("-", "")


def checkout_reference() -> str:
    """Providers restrict reference characters (Paystack: letters, digits, "-", ".", "="): hyphens only."""
    return f"riinox-sub-{uuid.uuid4().hex}"


def renewal_reference(subscription_pk: object, period_end: datetime, attempt: int) -> str:
    """Deterministic: a retried renewal job derives the same reference, so it can never double-charge."""
    return f"riinox-ren-{compact_id(subscription_pk)}-{int(period_end.timestamp())}-{attempt}"
