from django.core.mail import send_mail
from django.conf import settings
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta

def send_invitation_email(membership):
    """
    Sends an invitation email to the invited user with a unique token.
    """
    accept_url = f"{settings.FRONTEND_URL}/invite/accept/{membership.invite_token}"

    subject = f"You're invited to join {membership.organization.name}"
    message = (
        f"Hello,\n\n"
        f"You have been invited to join {membership.organization.name} "
        f"as a {membership.role}.\n\n"
        f"Click the link below to accept your invitation:\n"
        f"{accept_url}\n\n"
        f"This link will expire on {membership.expires_at.strftime('%Y-%m-%d %H:%M:%S')}.\n\n"
        f"Best regards,\n"
        f"The {settings.PROJECT_NAME} Team"
    )

    send_mail(
        subject,
        message,
        settings.DEFAULT_FROM_EMAIL,
        [membership.invited_email],
        fail_silently=False,
    )

def default_invite_expiry():
    """Default expiry for invitations (7 days from now)."""
    return timezone.now() + timedelta(days=7)
