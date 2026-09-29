from dataclasses import dataclass
from functools import lru_cache
from zoneinfo import ZoneInfo
from zoneinfo import ZoneInfoNotFoundError

import pycountry
from django_countries import countries


@lru_cache(maxsize=1)
def get_countries() -> tuple[dict[str, str], ...]:
    """
    ISO 3166-1 countries, sorted by name. Built once per process.
    """
    return tuple(
        sorted(
            ({"code": c.code, "name": str(c.name)} for c in countries),
            key=lambda c: c["name"],
        )
    )


@lru_cache(maxsize=1)
def get_country_names() -> dict[str, str]:
    return {c["code"]: c["name"] for c in get_countries()}


@lru_cache(maxsize=1)
def get_currencies() -> tuple[dict[str, str], ...]:
    """
    ISO 4217 currencies, sorted by name. Codes starting with "X" are
    precious metals, testing codes and supranational units (XAU, XTS,
    XXX...), not real base currencies, so they are excluded.
    """
    return tuple(
        sorted(
            (
                {"code": c.alpha_3, "name": c.name}
                for c in pycountry.currencies
                if not c.alpha_3.startswith("X")
            ),
            key=lambda c: c["name"],
        )
    )


@lru_cache(maxsize=1)
def get_currency_codes() -> frozenset[str]:
    return frozenset(c["code"] for c in get_currencies())


def is_valid_country(code: str) -> bool:
    return code.upper() in countries


def get_country_name(code: str) -> str:
    """Display name for a country code; falls back to the code itself."""
    return get_country_names().get(code.upper(), code)


def is_valid_currency(code: str) -> bool:
    return code.upper() in get_currency_codes()


@lru_cache(maxsize=512)
def is_valid_timezone(name: str) -> bool:
    """
    Asks the tz database directly instead of enumerating it, so it works on
    slim images whose zoneinfo directory is incomplete, and rejects path-like
    input ("../etc/passwd") that ZoneInfo refuses.
    """
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return False
    return True
