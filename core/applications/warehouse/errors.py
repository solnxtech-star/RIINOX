from django.db.models import TextChoices

from core.helper.custom_exceptions import BusinessRuleError


class LocationErrorCode(TextChoices):
    DEFAULT_LOCATION_PROTECTED = "DEFAULT_LOCATION_PROTECTED"
    LOCATION_INACTIVE = "LOCATION_INACTIVE"
    LOCATION_ACCESS_DENIED = "LOCATION_ACCESS_DENIED"
    LOCATION_REQUIRED = "LOCATION_REQUIRED"
    ALL_LOCATIONS_REQUIRED = "ALL_LOCATIONS_REQUIRED"


_E = LocationErrorCode
_SPECS: dict[str, tuple[int, str]] = {
    _E.DEFAULT_LOCATION_PROTECTED: (
        409,
        "This is the organization's default location. Make another location the default first.",
    ),
    _E.LOCATION_INACTIVE: (400, "An inactive location can't be the default. Reactivate it first."),
    _E.LOCATION_ACCESS_DENIED: (403, "You don't have access to this location."),
    _E.LOCATION_REQUIRED: (
        400,
        "Choose a location. Your account is limited to specific locations, so one must be given.",
    ),
    _E.ALL_LOCATIONS_REQUIRED: (
        403,
        "Only members with access to all locations can do this.",
    ),
}


def fail(code: LocationErrorCode, message: str | None = None, **extra) -> BusinessRuleError:
    status_code, default_message = _SPECS[code]
    return BusinessRuleError(code.value, message or default_message, status_code=status_code, extra=extra)
