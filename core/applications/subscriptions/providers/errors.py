class ProviderError(Exception):
    """Base class. Messages must never contain secrets, tokens, or card data."""


class ProviderRejectedError(ProviderError):
    """The provider definitively refused. Safe to mark the attempt FAILED."""

    def __init__(self, message: str, *, code: str = ""):
        super().__init__(message)
        self.code = code


class ProviderUnavailableError(ProviderError):
    """
    Outcome UNKNOWN (timeout, 5xx, rate limit, network). The attempt must stay
    PENDING; reconciliation settles it. Never mark it FAILED.
    """


class ProviderConfigurationError(ProviderError):
    """Bad credentials or setup: a deploy problem, not a customer problem. Alert on it."""


class TransactionNotFoundError(ProviderError):
    """The provider has no record of this reference."""


class InvalidWebhookSignatureError(ProviderError):
    """The request did not come from the provider. Respond 401 and do not store it."""


class MalformedProviderPayloadError(ProviderError):
    """An actionable payload was missing or had invalid required fields."""


class NoProviderForCurrencyError(ProviderError):
    """No configured provider can bill in this currency."""
