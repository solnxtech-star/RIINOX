from contextlib import contextmanager

from rest_framework.exceptions import PermissionDenied
from rest_framework.exceptions import ValidationError
from rest_framework.settings import api_settings

from core.applications.users import services

NON_FIELD = api_settings.NON_FIELD_ERRORS_KEY


@contextmanager
def domain_errors():
    try:
        yield
    except services.MembershipRuleViolation as exc:
        raise ValidationError({exc.field or NON_FIELD: [exc.message]}) from exc
    except services.BusinessTypeSelectionError as exc:
        raise ValidationError({"business_types": [str(exc)]}) from exc
    except services.InvitationInvalid as exc:
        raise ValidationError({NON_FIELD: [str(exc)]}) from exc
    except (services.NotAuthorized, services.PlanLimitReached) as exc:
        raise PermissionDenied(str(exc)) from exc
