from django.db import models
from django.conf import settings
from django.utils.translation import gettext_lazy as _
from core.helper.models import TimeBasedModel

from core.helper.enums import NotificationTypeChoices, NotificationStatusChoices
import auto_prefetch

class Notification(TimeBasedModel):
    """
    Notifications sent to users (email/SMS/system).
    """

    user = auto_prefetch.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications"
    )
    notification_type = models.CharField(max_length=50, choices=NotificationTypeChoices.choices)
    message = models.TextField()
    status = models.CharField(
        max_length=20, choices=NotificationStatusChoices.choices, default=NotificationStatusChoices.PENDING
    )

    class Meta:
        verbose_name = _("Notification")
        verbose_name_plural = _("Notifications")
        ordering = ["-created_at"]

    def __str__(self):
        return f"Notification to {self.user} - {self.notification_type}"


