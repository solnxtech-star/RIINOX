from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from enum import Enum
from typing import Any
from typing import Protocol


class PaymentOutcome(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    ABANDONED = "abandoned"   # customer never completed checkout (reported by verify)
    PENDING = "pending"       # not final; ask again later


@dataclass(frozen=True, slots=True)
class CheckoutSession:
    authorization_url: str
    reference: str


@dataclass(frozen=True, slots=True, repr=False)
class SavedMethod:
    """A reusable payment method. The token can charge the customer: never log it."""

    provider: str
    token: str
    customer_id: str
    customer_email: str
    fingerprint: str
    brand: str
    last4: str
    exp_month: int | None
    exp_year: int | None

    def __repr__(self) -> str:
        return f"SavedMethod({self.provider}, ••••{self.last4})"


@dataclass(frozen=True, slots=True)
class PaymentEvent:
    """
    Provider-neutral result of a payment, whether it came from a webhook, a
    verify call, or a synchronous charge response. The services only ever see
    this, so one code path settles payments no matter where the news arrived.
    """

    reference: str
    outcome: PaymentOutcome
    amount_minor: int
    currency: str
    event_type: str = ""
    paid_at: datetime | None = None
    saved_method: SavedMethod | None = None
    failure_code: str = ""
    failure_message: str = ""
    redacted_payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True, repr=False)
class WebhookEnvelope:
    event_id: str
    event_type: str
    payload: dict[str, Any]  # RAW: may contain tokens. Store only in the encrypted WebhookEvent.payload.

    def __repr__(self) -> str:
        return f"WebhookEnvelope({self.event_type}, {self.event_id})"


class PaymentProvider(Protocol):
    name: str
    supported_currencies: frozenset[str]

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
    ) -> CheckoutSession: ...

    def charge_saved_method(
        self,
        *,
        method_token: str,
        email: str,
        amount_minor: int,
        currency: str,
        reference: str,
        metadata: Mapping[str, Any],
    ) -> PaymentEvent: ...

    def verify_transaction(self, reference: str) -> PaymentEvent: ...

    def verify_webhook(self, *, body: bytes, headers: Mapping[str, str]) -> WebhookEnvelope: ...

    def parse_event(self, payload: Mapping[str, Any]) -> PaymentEvent | None:
        """Actionable payment event, or None for event types we deliberately ignore."""
        ...

    def redact(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """A copy safe to store in plain columns: no tokens, minimal personal data."""
        ...
