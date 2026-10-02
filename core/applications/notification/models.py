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

class ImmutableAuditLogError(Exception):
    """Audit events are append-only."""


class AuditLogQuerySet(auto_prefetch.QuerySet):
    def for_organization(self, organization):
        return self.filter(organization=organization)

    def update(self, **kwargs):
        raise ImmutableAuditLogError("Audit logs cannot be updated.")

    def delete(self):
        raise ImmutableAuditLogError("Audit logs cannot be deleted.")


AuditLogManager = auto_prefetch.Manager.from_queryset(AuditLogQuerySet)


class AuditLog(TimeBasedModel):
    """
    Immutable record of an attributable business action (PRD §31).
    Written in the same transaction as the change it describes, so a rollback
    never leaves an event for something that didn't happen.
    """

    # Nullable only for events with no tenant yet (e.g. a failed login).
    # PROTECT: audit history must outlive any attempt to delete the org.
    organization = auto_prefetch.ForeignKey(
        "users.Organization", on_delete=models.PROTECT, null=True, blank=True,
        related_name="audit_logs",
    )
    actor = auto_prefetch.ForeignKey(
        "users.User", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="audit_logs",
    )
    actor_email = models.CharField(max_length=254, blank=True)  # snapshot: survives user changes

    action = models.CharField(max_length=100)
    resource_type = models.CharField(max_length=100, blank=True)   # e.g. "users.invitation"
    resource_id = models.CharField(max_length=64, blank=True)
    resource_repr = models.CharField(max_length=255, blank=True)

    previous_values = models.JSONField(null=True, blank=True)
    new_values = models.JSONField(null=True, blank=True)
    reason = models.TextField(blank=True)
    # Becomes a real FK once the Approval model exists (§30).
    approval_id = models.UUIDField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    request_id = models.CharField(max_length=64, blank=True)       # §50 observability

    objects = AuditLogManager()

    class Meta(auto_prefetch.Model.Meta):
        verbose_name = _("Audit Log")
        verbose_name_plural = _("Audit Logs")
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["organization", "-created_at"]),
            models.Index(fields=["organization", "action"]),
            models.Index(fields=["resource_type", "resource_id"]),
            models.Index(fields=["actor", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.action} by {self.actor_email or 'system'} ({self.created_at:%Y-%m-%d %H:%M})"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ImmutableAuditLogError("Audit logs cannot be modified.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ImmutableAuditLogError("Audit logs cannot be deleted.")
