
import logging
from urllib.parse import urlencode

from django.conf import settings
from templated_mail.mail import BaseEmailMessage

from core.applications.notification.email import send_templated_email
from core.applications.users.token import default_token_generator

logger = logging.getLogger(__name__)
class ActivationEmail(BaseEmailMessage):
    template_name = "email/activation.html"

    def get_context_data(self):
        # ActivationEmail can be deleted
        context = super().get_context_data()

        user = context.get("user")
        context["token"] = default_token_generator.make_token(user)
        return context


class ConfirmationEmail(BaseEmailMessage):
    template_name = "email/confirmation.html"


class PasswordResetEmail(ActivationEmail):
    template_name = "email/password_reset.html"


class PasswordChangedConfirmationEmail(BaseEmailMessage):
    template_name = "email/password_changed_confirmation.html"


class UsernameChangedConfirmationEmail(BaseEmailMessage):
    template_name = "email/username_changed_confirmation.html"


class UsernameResetEmail(ActivationEmail):
    template_name = "email/username_reset.html"

    def get_context_data(self):
        context = super().get_context_data()

        user = context.get("user")
        context["token"] = default_token_generator.make_token(user)
        return context



TEMPLATE = "email/invitation"


def build_accept_url(raw_token: str) -> str:
    base = settings.FRONTEND_URL.rstrip("/")
    path = getattr(settings, "INVITATION_ACCEPT_PATH", "/accept-invite")
    return f"{base}{path}?{urlencode({'token': raw_token})}"


def deliver_invitation_email(invitation, raw_token: str) -> bool:
    """
    Sends the invitation email. Returns True on success and never raises.

    It runs after the transaction has committed, so a mail failure must not
    turn a successful invite into a 500. On failure the invitation stays
    pending and can be re-sent from the staff screen.
    """
    try:
        inviter = invitation.invited_by
        send_templated_email(
            to=invitation.email,
            template=TEMPLATE,
            context={
                "name": invitation.name,
                "organization_name": invitation.organization.name,
                "role_name": invitation.role.name,
                "inviter_name": (inviter.name or inviter.email) if inviter else "",
                "accept_url": build_accept_url(raw_token),
                "expires_at": invitation.expires_at,
                "app_name": getattr(settings, "APP_NAME", "RIINOX"),
            },
        )
    except Exception:  # noqa: BLE001 - any mail-layer failure must be contained here
        logger.exception("Invitation email failed invitation_id=%s", invitation.pk)
        return False

    logger.info("Invitation email sent invitation_id=%s", invitation.pk)
    return True
