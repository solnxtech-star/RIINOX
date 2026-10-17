from __future__ import annotations

import copy
import hashlib
import hmac
import json
from collections.abc import Mapping
from datetime import timezone as dt_timezone
from typing import Any
from urllib.parse import quote

import requests
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from core.applications.subscriptions.providers.base import CheckoutSession
from core.applications.subscriptions.providers.base import PaymentEvent
from core.applications.subscriptions.providers.base import PaymentOutcome
from core.applications.subscriptions.providers.base import SavedMethod
from core.applications.subscriptions.providers.base import WebhookEnvelope
from core.applications.subscriptions.providers.errors import (
    InvalidWebhookSignatureError,
)
from core.applications.subscriptions.providers.errors import (
    MalformedProviderPayloadError,
)
from core.applications.subscriptions.providers.errors import ProviderConfigurationError
from core.applications.subscriptions.providers.errors import ProviderRejectedError
from core.applications.subscriptions.providers.errors import ProviderUnavailableError
from core.applications.subscriptions.providers.errors import TransactionNotFoundError

DEFAULT_BASE_URL = "https://api.paystack.co"
# Which currencies work depends on the countries enabled on YOUR Paystack account;
# override with settings.PAYSTACK_CURRENCIES.
DEFAULT_CURRENCIES = frozenset({"NGN", "GHS", "ZAR", "KES", "USD"})

_REDACTED = "[redacted]"
_SENSITIVE_PATHS = (
    ("data", "authorization", "authorization_code"),
    ("data", "customer", "email"),
    ("data", "customer", "phone"),
    ("data", "customer", "first_name"),
    ("data", "customer", "last_name"),
)

_OUTCOMES = {
    "success": PaymentOutcome.SUCCESS,
    "failed": PaymentOutcome.FAILED,
    "reversed": PaymentOutcome.FAILED,
    "abandoned": PaymentOutcome.ABANDONED,
    # ongoing / pending / processing / queued / unknown -> PENDING (ask again later)
}


class PaystackProvider:
    name = "paystack"

    def __init__(
        self,
        *,
        secret_key: str,
        base_url: str = DEFAULT_BASE_URL,
        currencies: frozenset[str] = DEFAULT_CURRENCIES,
        timeout: tuple[float, float] = (3.05, 15.0),
    ):
        self._secret_key = secret_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self.supported_currencies = frozenset(currencies)

        # One pooled session per process. Only GETs are retried: a POST that
        # timed out may or may not have been processed, and retrying it blindly
        # is how customers get charged twice. Unknown outcomes are reconciled instead.
        retry = Retry(
            total=2,
            backoff_factor=0.3,
            status_forcelist=(502, 503, 504),
            allowed_methods=frozenset({"GET"}),
        )
        self._session = requests.Session()
        self._session.mount(
            "https://", HTTPAdapter(pool_connections=10, pool_maxsize=50, max_retries=retry)
        )

    def __repr__(self) -> str:  # never expose the secret key
        return "PaystackProvider()"

    # ------------------------------------------------------------------ #
    # Outbound API
    # ------------------------------------------------------------------ #
    def initialize_checkout(
        self,
        *,
        email: str,
        amount_minor: int,
        currency: str,
        reference: str,
        callback_url: str,
        metadata: Mapping[str, Any],
        save_payment_method: bool = True,
    ) -> CheckoutSession:
        payload: dict[str, Any] = {
            "email": email,
            "amount": int(amount_minor),
            "currency": currency,
            "reference": reference,
            "callback_url": callback_url,
            "metadata": dict(metadata),
        }
        if save_payment_method:
            # Only cards yield a reusable authorization (transfer/USSD cannot be auto-renewed).
            payload["channels"] = ["card"]

        data = self._request("POST", "/transaction/initialize", json=payload).get("data") or {}
        url = data.get("authorization_url")
        if not url:
            msg = "Paystack returned no authorization URL."
            raise ProviderUnavailableError(msg)
        return CheckoutSession(
            authorization_url=url, reference=data.get("reference") or reference
        )

    def charge_saved_method(
        self,
        *,
        method_token: str,
        email: str,
        amount_minor: int,
        currency: str,
        reference: str,
        metadata: Mapping[str, Any],
    ) -> PaymentEvent:
        body = self._request(
            "POST",
            "/transaction/charge_authorization",
            json={
                "authorization_code": method_token,
                "email": email,
                "amount": int(amount_minor),
                "currency": currency,
                "reference": reference,
                "metadata": dict(metadata),
            },
        )
        return self._event_from_transaction(
            body.get("data"), event_type="charge_authorization", payload=body
        )

    def verify_transaction(self, reference: str) -> PaymentEvent:
        try:
            body = self._request("GET", f"/transaction/verify/{quote(reference, safe='')}")
        except ProviderRejectedError as exc:
            if exc.code == "not_found":
                raise TransactionNotFoundError(reference) from exc
            raise
        return self._event_from_transaction(body.get("data"), event_type="verify", payload=body)

    # ------------------------------------------------------------------ #
    # Inbound webhooks
    # ------------------------------------------------------------------ #
    def verify_webhook(self, *, body: bytes, headers: Mapping[str, str]) -> WebhookEnvelope:
        signature = _header(headers, "x-paystack-signature")
        expected = hmac.new(self._secret_key.encode(), body, hashlib.sha512).hexdigest()
        if not signature or not hmac.compare_digest(expected.encode(), signature.strip().lower().encode()):
            msg = "Invalid Paystack webhook signature."
            raise InvalidWebhookSignatureError(msg)

        try:
            payload = json.loads(body)
        except ValueError as exc:
            msg = "Webhook body is not valid JSON."
            raise MalformedProviderPayloadError(msg) from exc
        if not isinstance(payload, dict):
            msg = "Webhook body is not a JSON object."
            raise MalformedProviderPayloadError(msg)

        event_type = str(payload.get("event") or "")
        data = payload.get("data")
        data_id = data.get("id") if isinstance(data, dict) else None
        # Paystack sends no event id: derive a stable one so redeliveries dedupe.
        event_id = f"{event_type}:{data_id}" if data_id is not None else f"sha256:{hashlib.sha256(body).hexdigest()}"
        return WebhookEnvelope(event_id=event_id, event_type=event_type, payload=payload)

    def parse_event(self, payload: Mapping[str, Any]) -> PaymentEvent | None:
        if payload.get("event") != "charge.success":
            return None  # everything else is deliberately ignored until it becomes actionable
        event = self._event_from_transaction(payload.get("data"), event_type="charge.success", payload=payload)
        if event.outcome is not PaymentOutcome.SUCCESS:
            msg = "charge.success event carried a non-success status."
            raise MalformedProviderPayloadError(msg)
        return event

    def redact(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        clean = copy.deepcopy(dict(payload))
        for path in _SENSITIVE_PATHS:
            node: Any = clean
            for key in path[:-1]:
                node = node.get(key) if isinstance(node, dict) else None
                if node is None:
                    break
            if isinstance(node, dict) and path[-1] in node:
                node[path[-1]] = _REDACTED
        return clean

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    def _request(
        self, method: str, path: str, *, json: dict[str, Any] | None = None
        ) -> dict[str, Any]:
        try:
            response = self._session.request(
                method,
                f"{self._base_url}{path}",
                json=json,
                headers={"Authorization": f"Bearer {self._secret_key}"},
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            # Class name only: never echo exception text into logs or error messages.
            msg = f"Paystack request failed ({exc.__class__.__name__})."
            raise ProviderUnavailableError(msg) from exc

        status = response.status_code
        if status in (401, 403):
            msg = "Paystack rejected the API credentials."
            raise ProviderConfigurationError(msg)
        if status == 429 or status >= 500:
            msg = f"Paystack unavailable (HTTP {status})."
            raise ProviderUnavailableError(msg)
        try:
            body = response.json()
        except ValueError as exc:
            msg = "Paystack returned a non-JSON response."
            raise ProviderUnavailableError(msg) from exc
        if not isinstance(body, dict):
            msg = "Paystack returned an unexpected response shape."
            raise ProviderUnavailableError(msg)

        if status == 404:
            msg = "Not found."
            raise ProviderRejectedError(msg, code="not_found")
        if status >= 400 or body.get("status") is not True:
            msg = str(body.get("message") or "Paystack rejected the request.")
            raise ProviderRejectedError(msg, code=str(status))
        return body

    def _event_from_transaction(
        self, data: Any, *, event_type: str, payload: Mapping[str, Any]
        ) -> PaymentEvent:
        if not isinstance(data, dict):
            msg = "Paystack payload has no transaction data."
            raise MalformedProviderPayloadError(msg)
        reference = str(data.get("reference") or "")
        amount_minor = _to_int(data.get("amount"))
        currency = str(data.get("currency") or "").upper()
        if not reference or amount_minor is None or not currency:
            msg = "Paystack payload is missing reference, amount, or currency."
            raise MalformedProviderPayloadError(msg)

        outcome = _OUTCOMES.get(
            str(data.get("status") or "").lower(), PaymentOutcome.PENDING
        )
        failed = outcome in (PaymentOutcome.FAILED, PaymentOutcome.ABANDONED)
        return PaymentEvent(
            reference=reference,
            outcome=outcome,
            amount_minor=amount_minor,
            currency=currency,
            event_type=event_type,
            paid_at=_parse_dt(data.get("paid_at")),
            saved_method=self._saved_method(data) if outcome is PaymentOutcome.SUCCESS else None,
            failure_code=str(data.get("status") or "") if failed else "",
            failure_message=str(data.get("gateway_response") or data.get("message") or "")[:500] if failed else "",
            redacted_payload=self.redact(payload),
        )

    def _saved_method(self, data: Mapping[str, Any]) -> SavedMethod | None:
        auth = data.get("authorization")
        customer = data.get("customer")
        if not isinstance(auth, dict) or not isinstance(customer, dict):
            return None
        token = str(auth.get("authorization_code") or "")
        email = str(customer.get("email") or "").strip().lower()
        # Not reusable (transfer/USSD/one-time card) or no email to charge it under: unusable for renewals.
        if not auth.get("reusable") or not token or not email:
            return None
        return SavedMethod(
            provider=self.name,
            token=token,
            customer_id=str(customer.get("customer_code") or ""),
            customer_email=email,
            fingerprint=str(auth.get("signature") or ""),
            brand=str(auth.get("brand") or auth.get("card_type") or "").strip(),
            last4=str(auth.get("last4") or "")[-4:],
            exp_month=_to_int(auth.get("exp_month")),
            exp_year=_to_int(auth.get("exp_year")),
        )


def _header(headers: Mapping[str, str], name: str) -> str:
    value = headers.get(name)
    if value is not None:
        return value
    lowered = name.lower()
    for key, val in headers.items():
        if key.lower() == lowered:
            return val
    return ""


def _to_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_dt(value: Any):
    if not value:
        return None
    parsed = parse_datetime(str(value))
    if parsed is not None and timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, dt_timezone.utc)
    return parsed
