from django.db.models import TextChoices

from core.helper.custom_exceptions import BusinessRuleError



class SubscriptionErrorCode(TextChoices):
    SUBSCRIPTION_REQUIRED = "SUBSCRIPTION_REQUIRED"
    SUBSCRIPTION_ALREADY_EXISTS = "SUBSCRIPTION_ALREADY_EXISTS"
    FEATURE_NOT_AVAILABLE = "FEATURE_NOT_AVAILABLE"


_E = SubscriptionErrorCode
_SPECS: dict[str, tuple[int, str]] = {
    _E.SUBSCRIPTION_REQUIRED: (403, "This organization has no subscription."),
    _E.SUBSCRIPTION_ALREADY_EXISTS: (409, "This organization already has a subscription."),
    _E.FEATURE_NOT_AVAILABLE: (403, "Your plan does not include this feature."),
}


def fail(code: SubscriptionErrorCode, message: str | None = None, **extra) -> BusinessRuleError:
    status_code, default_message = _SPECS[code]
    return BusinessRuleError(code.value, message or default_message, status_code=status_code, extra=extra)
