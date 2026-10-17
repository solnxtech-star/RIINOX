from __future__ import annotations

from functools import lru_cache

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from core.applications.subscriptions.providers.base import PaymentProvider
from core.applications.subscriptions.providers.fake import FakeProvider
from core.applications.subscriptions.providers.paystack import DEFAULT_BASE_URL
from core.applications.subscriptions.providers.paystack import DEFAULT_CURRENCIES
from core.applications.subscriptions.providers.paystack import PaystackProvider


def _build_paystack() -> PaymentProvider:
    secret_key = getattr(settings, "PAYSTACK_SECRET_KEY", "")
    if not secret_key:
        msg = "PAYSTACK_SECRET_KEY is not set."
        raise ImproperlyConfigured(msg)
    return PaystackProvider(
        secret_key=secret_key,
        base_url=getattr(settings, "PAYSTACK_BASE_URL", DEFAULT_BASE_URL),
        currencies=frozenset(getattr(settings, "PAYSTACK_CURRENCIES", DEFAULT_CURRENCIES)),
    )


def _build_fake() -> PaymentProvider:
    if not (settings.DEBUG or getattr(settings, "BILLING_ALLOW_FAKE_PROVIDER", False)):
        msg = "The fake billing provider is disabled outside DEBUG/tests."
        raise ImproperlyConfigured(msg)
    return FakeProvider()


_FACTORIES = {"paystack": _build_paystack, "fake": _build_fake}


@lru_cache(maxsize=None)
def get_provider(name: str) -> PaymentProvider:
    try:
        factory = _FACTORIES[name]
    except KeyError:
        msg = f"Unknown billing provider {name!r}."
        raise ImproperlyConfigured(msg) from None
    return factory()


def provider_for_currency(currency: str) -> PaymentProvider:
    """Route by currency using settings.BILLING_PROVIDER_BY_CURRENCY ("*" is the fallback)."""
    code = currency.upper()
    routing = getattr(settings, "BILLING_PROVIDER_BY_CURRENCY", {})
    name = routing.get(code) or routing.get("*")
    if not name:
        msg = f"No billing provider configured for currency {code!r}."
        raise ImproperlyConfigured(msg)
    provider = get_provider(name)
    if code not in provider.supported_currencies:
        msg = f"Provider {name!r} does not support currency {code!r}."
        raise ImproperlyConfigured(msg)
    return provider


def reset_providers() -> None:
    """Drop cached instances (tests that override settings call this)."""
    get_provider.cache_clear()
