from contextlib import contextmanager

from django.db.models import TextChoices
from rest_framework.exceptions import PermissionDenied
from rest_framework.exceptions import ValidationError
from rest_framework.settings import api_settings

from core.applications.users import services
from core.helper.custom_exceptions import BusinessRuleError

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


class InvitationErrorCode(TextChoices):
    ORGANIZATION_NOT_FOUND = "ORGANIZATION_NOT_FOUND"
    NOT_ORGANIZATION_MEMBER = "NOT_ORGANIZATION_MEMBER"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    INVALID_ROLE = "INVALID_ROLE"
    ROLE_NOT_ASSIGNABLE = "ROLE_NOT_ASSIGNABLE"
    ALREADY_MEMBER = "ALREADY_MEMBER"
    MEMBERSHIP_INACTIVE = "MEMBERSHIP_INACTIVE"
    ALREADY_INVITED = "ALREADY_INVITED"
    USER_LIMIT_REACHED = "USER_LIMIT_REACHED"
    INVITATION_NOT_FOUND = "INVITATION_NOT_FOUND"
    INVITATION_NOT_PENDING = "INVITATION_NOT_PENDING"
    INVITATION_RESEND_TOO_SOON = "INVITATION_RESEND_TOO_SOON"
    INVITATION_INVALID = "INVITATION_INVALID"
    INVITATION_EXPIRED = "INVITATION_EXPIRED"
    INVITATION_EMAIL_MISMATCH = "INVITATION_EMAIL_MISMATCH"
    EMAIL_NOT_VERIFIED = "EMAIL_NOT_VERIFIED"
    NOT_AUTHENTICATED = "NOT_AUTHENTICATED"


_E = InvitationErrorCode
_SPECS: dict[str, tuple[int, str]] = {
    _E.ORGANIZATION_NOT_FOUND: (404, "Organization not found."),
    _E.NOT_ORGANIZATION_MEMBER: (404, "Organization not found."),  # 404, never 403: don't reveal which orgs exist
    _E.PERMISSION_DENIED: (403, "Your role does not allow this action."),
    _E.INVALID_ROLE: (400, "The selected role is not valid for this organization."),
    _E.ROLE_NOT_ASSIGNABLE: (403, "You are not allowed to assign this role."),
    _E.ALREADY_MEMBER: (409, "This person is already a member of the organization."),
    _E.MEMBERSHIP_INACTIVE: (409, "This person is a deactivated member. Reactivate them instead of inviting."),
    _E.ALREADY_INVITED: (409, "A pending invitation already exists for this email. Resend it instead."),
    _E.USER_LIMIT_REACHED: (403, "Your plan's user limit has been reached."),
    _E.INVITATION_NOT_FOUND: (404, "Invitation not found."),
    _E.INVITATION_NOT_PENDING: (409, "Only pending invitations can be changed."),
    _E.INVITATION_RESEND_TOO_SOON: (429, "This invitation was sent recently. Try again shortly."),
    _E.INVITATION_INVALID: (404, "Invalid invitation."),
    _E.INVITATION_EXPIRED: (410, "This invitation has expired."),
    _E.INVITATION_EMAIL_MISMATCH: (403, "This invitation was sent to a different email address."),
    _E.EMAIL_NOT_VERIFIED: (403, "Verify your email address before accepting an invitation."),
}


def fail(code: InvitationErrorCode, message: str | None = None, **extra) -> BusinessRuleError:
    status_code, default_message = _SPECS[code]
    return BusinessRuleError(code.value, message or default_message, status_code=status_code, extra=extra)
