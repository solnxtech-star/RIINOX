from django.db.models import TextChoices

from core.helper.custom_exceptions import BusinessRuleError


class SubscriptionErrorCode(TextChoices):
    SUBSCRIPTION_REQUIRED = "SUBSCRIPTION_REQUIRED"
    SUBSCRIPTION_ALREADY_EXISTS = "SUBSCRIPTION_ALREADY_EXISTS"
    FEATURE_NOT_AVAILABLE = "FEATURE_NOT_AVAILABLE"
    PLAN_LIMIT_REACHED = "PLAN_LIMIT_REACHED"
    PLAN_NOT_AVAILABLE = "PLAN_NOT_AVAILABLE"
    PLAN_IS_FREE = "PLAN_IS_FREE"
    UNSUPPORTED_CURRENCY = "UNSUPPORTED_CURRENCY"
    CHECKOUT_FAILED = "CHECKOUT_FAILED"
    PLAN_PRICE_NOT_FOUND = "PLAN_PRICE_NOT_FOUND"
    PLAN_CHANGE_NOT_ALLOWED = "PLAN_CHANGE_NOT_ALLOWED"
    DUPLICATE_REQUEST = "DUPLICATE_REQUEST"
    INVALID_CALLBACK_URL = "INVALID_CALLBACK_URL"
    SUBSCRIPTION_INVALID_STATE = "SUBSCRIPTION_INVALID_STATE"
    PAYMENT_METHOD_NOT_FOUND = "PAYMENT_METHOD_NOT_FOUND"
    USAGE_EXCEEDS_PLAN = "USAGE_EXCEEDS_PLAN"


_E = SubscriptionErrorCode
_SPECS: dict[str, tuple[int, str]] = {
    _E.SUBSCRIPTION_REQUIRED: (
        403, "This organization has no subscription."
    ),
    _E.SUBSCRIPTION_ALREADY_EXISTS: (409, "This organization already has a subscription."),
    _E.FEATURE_NOT_AVAILABLE: (403, "Your plan does not include this feature."),
    _E.PLAN_LIMIT_REACHED: (
        403, "Your plan limit has been reached. Upgrade your plan to continue."
        ),
    _E.PLAN_NOT_AVAILABLE: (400, "This plan is not currently available."),
    _E.PLAN_IS_FREE: (400, "This plan is free and doesn't require checkout."),
    _E.UNSUPPORTED_CURRENCY: (400, "Payments in this currency aren't supported yet."),
    _E.CHECKOUT_FAILED: (502, "The payment provider could not complete this request."),
    _E.PLAN_PRICE_NOT_FOUND: (400, "This plan has no price for the selected currency and billing interval."),
    _E.PLAN_CHANGE_NOT_ALLOWED: (409, "This plan change isn't possible right now."),
    _E.DUPLICATE_REQUEST: (409, "This request was already made with the same idempotency key."),
    _E.INVALID_CALLBACK_URL: (400, "The callback URL must be an allowed HTTPS address."),
    _E.SUBSCRIPTION_INVALID_STATE: (409, "The subscription is not in a state that allows this."),
    _E.PAYMENT_METHOD_NOT_FOUND: (404, "Payment method not found."),
    _E.USAGE_EXCEEDS_PLAN: (409, "Your current usage is above this plan's limits. Reduce it first."),
}


def fail(
    code: SubscriptionErrorCode, message: str | None = None, **extra
    ) -> BusinessRuleError:
    status_code, default_message = _SPECS[code]
    return BusinessRuleError(
        code.value, message or default_message, status_code=status_code, extra=extra
    )
