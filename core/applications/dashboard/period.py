"""
The dashboard's period picker ("Today", "This Month", ...) resolved to concrete
dates in the organization's calendar, plus the period it is compared against.

"To-date" periods compare like with like: on 17 Nov, "This Month" is 1-17 Nov
compared with 1-17 Oct, not with the whole of October.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date
from datetime import timedelta

from rest_framework.exceptions import ValidationError

from core.helper.org_time import organization_today

MAX_CUSTOM_DAYS = 366
DEFAULT_PERIOD = "this_month"

PERIODS = {
    "today": "Today",
    "yesterday": "Yesterday",
    "this_week": "This Week",
    "last_7_days": "Last 7 Days",
    "this_month": "This Month",
    "last_month": "Last Month",
    "last_30_days": "Last 30 Days",
    "this_quarter": "This Quarter",
    "this_year": "This Year",
    "custom": "Custom Range",
}


@dataclass(frozen=True)
class Period:
    key: str
    label: str
    start: date
    end: date
    previous_start: date
    previous_end: date

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "start_date": self.start.isoformat(),
            "end_date": self.end.isoformat(),
            "previous_start_date": self.previous_start.isoformat(),
            "previous_end_date": self.previous_end.isoformat(),
        }


def _shift_months(day: date, months: int) -> date:
    """Same day-of-month `months` away, clamped to the month's length (31 Mar -> 28/29 Feb)."""
    month_index = day.month - 1 + months
    year, month = day.year + month_index // 12, month_index % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def _previous_window(start: date, end: date) -> tuple[date, date]:
    """The same number of days immediately before `start`."""
    length = (end - start).days + 1
    return start - timedelta(days=length), start - timedelta(days=1)


def _parse_date(params, name: str) -> date:
    raw = params.get(name)
    if not raw:
        raise ValidationError({name: ["Required when period=custom (YYYY-MM-DD)."]})
    try:
        return date.fromisoformat(raw.strip())
    except ValueError:
        raise ValidationError({name: ["Use YYYY-MM-DD."]}) from None


def resolve_period(params, organization) -> Period:
    key = (params.get("period") or DEFAULT_PERIOD).strip().lower()
    if key not in PERIODS:
        raise ValidationError({"period": [f"Must be one of: {', '.join(PERIODS)}."]})
    today = organization_today(organization)

    if key == "today":
        start = end = today
        prev = (today - timedelta(days=1),) * 2
    elif key == "yesterday":
        start = end = today - timedelta(days=1)
        prev = (start - timedelta(days=1),) * 2
    elif key == "this_week":  # Monday to today, vs the same weekdays last week
        start, end = today - timedelta(days=today.weekday()), today
        prev = (start - timedelta(days=7), end - timedelta(days=7))
    elif key == "last_7_days":
        start, end = today - timedelta(days=6), today
        prev = _previous_window(start, end)
    elif key == "this_month":
        start, end = today.replace(day=1), today
        prev = (_shift_months(start, -1), _shift_months(end, -1))
    elif key == "last_month":
        end = today.replace(day=1) - timedelta(days=1)
        start = end.replace(day=1)
        prev_end = start - timedelta(days=1)
        prev = (prev_end.replace(day=1), prev_end)
    elif key == "last_30_days":
        start, end = today - timedelta(days=29), today
        prev = _previous_window(start, end)
    elif key == "this_quarter":
        start, end = date(today.year, 3 * ((today.month - 1) // 3) + 1, 1), today
        prev = (_shift_months(start, -3), _shift_months(end, -3))
    elif key == "this_year":
        start, end = date(today.year, 1, 1), today
        prev = (date(today.year - 1, 1, 1), _shift_months(end, -12))
    else:  # custom
        start, end = _parse_date(params, "start_date"), _parse_date(params, "end_date")
        if end < start:
            raise ValidationError({"end_date": ["Must be on or after start_date."]})
        if end > today:
            raise ValidationError({"end_date": ["Can't be in the future."]})
        if (end - start).days + 1 > MAX_CUSTOM_DAYS:
            raise ValidationError({"end_date": [f"A range can cover at most {MAX_CUSTOM_DAYS} days."]})
        prev = _previous_window(start, end)

    return Period(key, PERIODS[key], start, end, prev[0], prev[1])
