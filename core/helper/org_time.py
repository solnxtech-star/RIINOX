"""
An organization's own calendar (PRD §7): "today", day boundaries and periods are
the tenant's, not the server's (UTC). A sale at 00:30 in Lagos is "today" in
Lagos even though it is still "yesterday" in UTC.
"""

from __future__ import annotations

import logging
from datetime import date
from datetime import datetime
from datetime import time
from datetime import timedelta
from datetime import tzinfo
from zoneinfo import ZoneInfo
from zoneinfo import ZoneInfoNotFoundError

from django.utils import timezone

logger = logging.getLogger(__name__)


def organization_tz(organization) -> tzinfo:
    """The organization's IANA timezone, or the project timezone when unset/invalid."""
    raw = getattr(organization, "timezone", None)
    if raw:
        try:
            return ZoneInfo(str(raw))
        except (ZoneInfoNotFoundError, ValueError, OSError):
            logger.warning(
                "organization.invalid_timezone",
                extra={"organization_id": str(getattr(organization, "pk", "")), "timezone": str(raw)},
            )
    return timezone.get_current_timezone()


def organization_today(organization) -> date:
    return timezone.now().astimezone(organization_tz(organization)).date()


def day_start(day: date, tz: tzinfo) -> datetime:
    """Midnight at the start of `day` in `tz`, as an aware datetime."""
    return datetime.combine(day, time.min, tzinfo=tz)


def day_range(start: date, end: date, tz: tzinfo) -> tuple[datetime, datetime]:
    """Half-open [start 00:00, end+1 00:00) in `tz`, for index-friendly datetime filters."""
    return day_start(start, tz), day_start(end + timedelta(days=1), tz)
