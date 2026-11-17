import auto_prefetch
from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils.translation import gettext_lazy as _

from core.helper.models import TimeBasedModel


class Notification(TimeBasedModel):
    """
    One in-app notification for one person (PRD §33). `notification_type` is an
    event code from notification/events.py. `reference` points at whatever
    triggered it (a Product for low stock, a StockAdjustmentRequest for a
    pending approval, ...). `link` tells the UI which screen to open.
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
    notification_type = models.CharField(max_length=50)
    title = models.CharField(max_length=200)
    message = models.TextField()
    location = auto_prefetch.ForeignKey(
        "warehouse.Warehouse",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    content_type = auto_prefetch.ForeignKey(
        ContentType,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    # Text, not an integer: every RIINOX model has a UUID primary key.
    object_id = models.CharField(max_length=64, blank=True, default="")
    reference = GenericForeignKey("content_type", "object_id")
    link = models.JSONField(null=True, blank=True)
    data = models.JSONField(default=dict, blank=True)
    # Same key within the dedupe window = the same alert; it isn't sent again.
    dedupe_key = models.CharField(max_length=200, blank=True)
    # Grouped events (events.EventSpec.group_message): the one unread row per
    # person+event that new occurrences update. Blank for ordinary notifications.
    group_key = models.CharField(max_length=100, blank=True, default="")
    is_read = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta(auto_prefetch.Model.Meta):
        verbose_name = _("Notification")
        verbose_name_plural = _("Notifications")
        ordering = ["-created_at"]
        constraints = [
            # At most one open (unread) group per person and event, even under
            # concurrent sales: the loser of a race updates the winner's row.
            models.UniqueConstraint(
                fields=["organization", "recipient", "notification_type", "group_key"],
                condition=models.Q(is_read=False) & ~models.Q(group_key=""),
                name="unique_open_notification_group",
            ),
        ]
        indexes = [
            # The bell: one person's notifications in one organization, newest first.
            models.Index(fields=["recipient", "organization", "-created_at"], name="notif_inbox_idx"),
            models.Index(fields=["recipient", "organization", "is_read"], name="notif_unread_idx"),
            models.Index(fields=["organization", "notification_type", "dedupe_key", "created_at"],
                         name="notif_dedupe_idx"),
        ]

    def __str__(self):
        return f"{self.notification_type} → {self.recipient}"


class NotificationSetting(TimeBasedModel):
    """
    An organization's choices for one event (the Notifications settings screen).
    Only events the organization changed have a row; the rest use the defaults
    in notification/events.py.
    """

    organization = auto_prefetch.ForeignKey(
        "users.Organization",
        on_delete=models.CASCADE,
        related_name="notification_settings",
    )
    event = models.CharField(max_length=50)
    enabled = models.BooleanField(default=True)
    in_app = models.BooleanField(default=True)
    email = models.BooleanField(default=True)
    recipient_roles = models.ManyToManyField("users.Role", blank=True, related_name="+")
    options = models.JSONField(default=dict, blank=True)
    updated_by = auto_prefetch.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta(auto_prefetch.Model.Meta):
        verbose_name = _("Notification Setting")
        verbose_name_plural = _("Notification Settings")
        constraints = [
            models.UniqueConstraint(fields=["organization", "event"], name="unique_notification_setting"),
        ]

    def __str__(self):
        return f"{self.organization_id}:{self.event}"


class UserNotificationPreference(TimeBasedModel):
    """
    One person's own choice for one event in one organization ("My notifications").
    It can only switch delivery *off* for themselves: an event the organization
    disabled or doesn't send to their role never reaches them, whatever this says.
    Only events a person changed have a row.
    """

    user = auto_prefetch.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notification_preferences",
    )
    organization = auto_prefetch.ForeignKey(
        "users.Organization",
        on_delete=models.CASCADE,
        related_name="+",
    )
    event = models.CharField(max_length=50)
    in_app = models.BooleanField(default=True, help_text=_("In-app, including the live pop-up."))
    email = models.BooleanField(default=True)

    class Meta(auto_prefetch.Model.Meta):
        verbose_name = _("User Notification Preference")
        verbose_name_plural = _("User Notification Preferences")
        constraints = [
            models.UniqueConstraint(fields=["user", "organization", "event"], name="unique_user_notification_pref"),
        ]
        indexes = [models.Index(fields=["organization", "event", "user"], name="notif_pref_lookup_idx")]

    def __str__(self):
        return f"{self.user_id}:{self.organization_id}:{self.event}"

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
