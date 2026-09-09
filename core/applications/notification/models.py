import auto_prefetch
from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils.translation import gettext_lazy as _

from core.helper.enums import NotificationTypeChoices
from core.helper.models import TimeBasedModel


class Notification(TimeBasedModel):
    """
    In-app notification for Admin and Staff dashboards (PRD §18, §19,
    §25, §26, §33). `reference` generically points at whatever triggered
    it — a Product for LOW_STOCK, an Invoice for INVOICE_CANCELLED, a
    StockAdjustmentRequest for PENDING_APPROVAL, etc.
    """

    organization = auto_prefetch.ForeignKey(
        "users.Organization",
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    recipient = auto_prefetch.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    notification_type = models.CharField(
        max_length=30,
        choices=NotificationTypeChoices.choices,
    )
    title = models.CharField(max_length=200)
    message = models.TextField()
    content_type = auto_prefetch.ForeignKey(
        ContentType,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    object_id = models.PositiveIntegerField(null=True, blank=True)
    reference = GenericForeignKey("content_type", "object_id")
    is_read = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta(auto_prefetch.Model.Meta):
        verbose_name = _("Notification")
        verbose_name_plural = _("Notifications")
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["recipient", "is_read"])]

    def __str__(self):
        return f"{self.notification_type} → {self.recipient}"