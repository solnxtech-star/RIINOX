from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from core.applications.subscriptions.providers.base import CheckoutSession
from core.applications.subscriptions.providers.base import PaymentEvent
from core.applications.subscriptions.providers.base import PaymentOutcome
from core.applications.subscriptions.providers.base import SavedMethod
from core.applications.subscriptions.providers.base import WebhookEnvelope
from core.applications.subscriptions.providers.errors import (
    InvalidWebhookSignatureError,
)
from core.applications.subscriptions.providers.errors import ProviderError
from core.applications.subscriptions.providers.errors import TransactionNotFoundError


class FakeProvider:
    """In-memory provider for tests and local development. No network; fully scriptable."""

    name = "fake"
    supported_currencies = frozenset({"NGN", "USD", "GHS", "ZAR", "KES"})
    SIGNATURE = "fake-signature"

    def __init__(self) -> None:
        self.transactions: dict[str, PaymentEvent] = {}
        self.charge_outcome = PaymentOutcome.SUCCESS
        self.charge_error: ProviderError | None = None
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def initialize_checkout(self, *, email, amount_minor,
                            currency, reference, callback_url, metadata,
                            save_payment_method=True
        ) -> CheckoutSession:
        self.calls.append(
            ("initialize_checkout", {"reference": reference, "amount_minor": amount_minor})
        )
        self.transactions[reference] = PaymentEvent(
            reference, PaymentOutcome.PENDING, amount_minor, currency
        )
        return CheckoutSession(f"https://fake.test/pay/{reference}", reference)

    def charge_saved_method(
        self, *, method_token, email, amount_minor, currency, reference, metadata
    ) -> PaymentEvent:
        self.calls.append(
            ("charge_saved_method", {"reference": reference, "amount_minor": amount_minor})
        )
        if self.charge_error is not None:
            raise self.charge_error
        failed = self.charge_outcome is not PaymentOutcome.SUCCESS
        event = PaymentEvent(
            reference,
            self.charge_outcome,
            amount_minor, currency, event_type="charge_authorization",
            failure_code=self.charge_outcome.value if failed else "",
            failure_message="Declined" if failed else "",
        )
        self.transactions[reference] = event
        return event

    def verify_transaction(self, reference: str) -> PaymentEvent:
        try:
            return self.transactions[reference]
        except KeyError:
            raise TransactionNotFoundError(reference) from None

    # --- test helpers ---------------------------------------------------
    def settle(self, reference: str, outcome: PaymentOutcome = PaymentOutcome.SUCCESS,
               saved_method: SavedMethod | None = None) -> PaymentEvent:
        """Simulate the customer finishing (or abandoning) checkout."""
        pending = self.transactions[reference]
        event = PaymentEvent(
            reference, outcome,
            pending.amount_minor, pending.currency,
            event_type="charge.success",
            saved_method=saved_method if outcome is PaymentOutcome.SUCCESS else None,
        )
        self.transactions[reference] = event
        return event

    def build_webhook(self, event: PaymentEvent) -> tuple[bytes, dict[str, str]]:
        data: dict[str, Any] = {
            "reference": event.reference,
            "amount_minor": event.amount_minor,
            "currency": event.currency
        }
        if event.saved_method:
            data["method"] = {
                "token": event.saved_method.token,
                "customer_email": event.saved_method.customer_email,
                "last4": event.saved_method.last4,
            }
        body = json.dumps({
            "event": "charge.success", "id": f"fake-{event.reference}", "data": data
        }).encode()
        return body, {"x-fake-signature": self.SIGNATURE}

    # --- inbound --------------------------------------------------------
    def verify_webhook(self, *, body: bytes, headers: Mapping[str, str]) -> WebhookEnvelope:
        if headers.get("x-fake-signature") != self.SIGNATURE:
            msg = "Invalid fake webhook signature."
            raise InvalidWebhookSignatureError(msg)
        payload = json.loads(body)
        return WebhookEnvelope(
            event_id=payload["id"], event_type=payload["event"], payload=payload
        )

    def parse_event(self, payload: Mapping[str, Any]) -> PaymentEvent | None:
        if payload.get("event") != "charge.success":
            return None
        data = payload["data"]
        method = data.get("method")
        saved = (
            SavedMethod(
                "fake", method["token"],
                "fake-customer",
                method["customer_email"], "fake-fp",
                "visa", method["last4"], 12, 2030
            )
            if method else None
        )
        return PaymentEvent(
            data["reference"],
            PaymentOutcome.SUCCESS,
            data["amount_minor"], data["currency"],
            event_type="charge.success", saved_method=saved
        )

    def redact(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        clean = json.loads(json.dumps(payload))
        method = clean.get("data", {}).get("method")
        if method:
            method["token"] = "[redacted]"
        return clean
