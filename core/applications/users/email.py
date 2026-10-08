from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from urllib.parse import urlencode

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from templated_mail.mail import BaseEmailMessage

from core.applications.users.token import default_token_generator

if TYPE_CHECKING:
    from core.applications.users.models import Invitation

logger = logging.getLogger(__name__)

__all__ = [
    "ActivationEmail",
    "ConfirmationEmail",
    "InvitationEmail",
    "PasswordChangedConfirmationEmail",
    "PasswordResetEmail",
    "UsernameChangedConfirmationEmail",
    "UsernameResetEmail",
    "build_accept_url",
    "deliver_invitation_email",
]


# --------------------------------------------------------------------------- #
# Account emails (Djoser)
# --------------------------------------------------------------------------- #
class ActivationEmail(BaseEmailMessage):
    """Also the base of the reset emails: it adds the account `token` to the context."""

    template_name = "email/activation.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["token"] = default_token_generator.make_token(context.get("user"))
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
    # The token comes from ActivationEmail.get_context_data; the override this
    # class used to carry was an exact copy of it.
    template_name = "email/username_reset.html"


# --------------------------------------------------------------------------- #
# Invitation email
# --------------------------------------------------------------------------- #
class InvitationEmail(BaseEmailMessage):
    template_name = "email/invitation.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.setdefault("app_name", getattr(settings, "APP_NAME", "RIINOX"))
        return context


def build_accept_url(raw_token: str) -> str:
    """The frontend link the invitee opens. The raw token only ever exists here and in the email."""
    base = settings.FRONTEND_URL.rstrip("/")
    path = getattr(settings, "INVITATION_ACCEPT_PATH", "/accept-invite")
    return f"{base}{path}?{urlencode({'token': raw_token})}"


def _invitation_context(invitation: Invitation, raw_token: str) -> dict:
    inviter = invitation.invited_by
    return {
        "name": invitation.name,
        "organization_name": invitation.organization.name,
        "role_name": invitation.role.name,
        "inviter_name": (inviter.name or inviter.email) if inviter else "",
        "accept_url": build_accept_url(raw_token),
        "expires_at": invitation.expires_at,
    }


def deliver_invitation_email(invitation: Invitation, raw_token: str) -> bool:
    ctx = _invitation_context(invitation, raw_token)
    ctx.setdefault("app_name", getattr(settings, "APP_NAME", "RIINOX"))

    try:
        html = render_to_string("email/invitation.html", ctx)
        text = render_to_string("email/invitation.txt", ctx)  # see below

        msg = EmailMultiAlternatives(
            subject=f"You're invited to join {ctx['organization_name']} on {ctx['app_name']}",
            body=text,
            to=[invitation.email],
        )
        msg.attach_alternative(html, "text/html")
        msg.send()
    except Exception:  # noqa: BLE001
        logger.exception("Invitation email failed invitation_id=%s", invitation.pk)
        return False

    logger.info("Invitation email sent invitation_id=%s", invitation.pk)
    return True
