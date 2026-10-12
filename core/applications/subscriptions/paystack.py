from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from decimal import Decimal

import requests
from django.conf import settings

PAYSTACK_BASE_URL = "https://api.paystack.co"

# Paystack settles in whichever currencies your merchant account is enabled
# for. NGN is the only one assumed configured by default; others are a
# dashboard setting away but must be explicitly enabled before use.
SUPPORTED_CURRENCIES = frozenset({"NGN"})

_TIMEOUT_SECONDS = 10  # Paystack itself recommends failing fast and retrying client-side.


class PaystackError(Exception):
    """Base for every error this module raises."""


class PaystackRequestFailed(PaystackError):
    """The HTTP call succeeded but Paystack reported status: false, or the call itself failed."""


class PaystackSignatureInvalid(PaystackError):
    """The webhook's x-paystack-signature header doesn't match the payload."""


class UnsupportedCurrency(PaystackError):
    """The organization's currency isn't one this Paystack integration is configured for."""


@dataclass(frozen=True, slots=True)
class CheckoutSession:
    authorization_url: str
    access_code: str
    reference: str


def _secret_key() -> str:
    key = getattr(settings, "PAYSTACK_SECRET_KEY", "")
    if not key:
        raise PaystackError("PAYSTACK_SECRET_KEY is not configured.")
    return key


def _session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"Authorization": f"Bearer {_secret_key()}", "Content-Type": "application/json"})
    return session


def to_subunit(amount: Decimal) -> int:
    """Naira -> kobo (Paystack's API is always in the currency's smallest unit)."""
    return int((amount * 100).to_integral_value())


def initialize_transaction(
    *, email: str, amount: Decimal, currency: str, reference: str, callback_url: str, metadata: dict
) -> CheckoutSession:
    """
    Start a payment. `reference` must be unique (it's your idempotency key on
    the webhook side too — generate it, don't let Paystack pick one), and
    `amount` is in the plan's major unit (Naira); this function converts it.
    """
    if currency not in SUPPORTED_CURRENCIES:
        raise UnsupportedCurrency(f"'{currency}' is not enabled on this Paystack integration.")

    response = _session().post(
        f"{PAYSTACK_BASE_URL}/transaction/initialize",
        json={
            "email": email,
            "amount": to_subunit(amount),
            "currency": currency,
            "reference": reference,
            "callback_url": callback_url,
            "metadata": metadata,
        },
        timeout=_TIMEOUT_SECONDS,
    )
    body = _unwrap(response)
    return CheckoutSession(
        authorization_url=body["authorization_url"], access_code=body["access_code"], reference=reference
    )


def verify_transaction(reference: str) -> dict:
    """
    GET /transaction/verify/:reference — the source of truth for a payment's
    actual status. Not called from the webhook's hot path (see services.py for
    why), but kept here for a reconciliation job, a "check my payment" button,
    or manual support use.
    """
    response = _session().get(f"{PAYSTACK_BASE_URL}/transaction/verify/{reference}", timeout=_TIMEOUT_SECONDS)
    return _unwrap(response)


def _unwrap(response: requests.Response) -> dict:
    try:
        body = response.json()
    except ValueError as exc:
        raise PaystackRequestFailed(f"Paystack returned a non-JSON response (HTTP {response.status_code}).") from exc

    if not response.ok or not body.get("status"):
        raise PaystackRequestFailed(body.get("message", f"Paystack request failed (HTTP {response.status_code})."))
    return body["data"]


def verify_webhook_signature(payload: bytes, signature: str | None) -> None:
    """
    Paystack signs the exact raw request body with HMAC-SHA512 using your
    secret key, sent as `x-paystack-signature`. This MUST run against the raw
    bytes Django received — not a re-serialized dict, which can reorder keys
    or change whitespace and silently break the comparison. Constant-time
    compare (hmac.compare_digest) so response timing can't leak the secret.
    """
    if not signature:
        raise PaystackSignatureInvalid("Missing x-paystack-signature header.")
    expected = hmac.new(_secret_key().encode(), payload, hashlib.sha512).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise PaystackSignatureInvalid("Signature does not match payload.")
