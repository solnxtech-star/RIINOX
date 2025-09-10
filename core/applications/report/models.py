from django.db import models

# Create your models here.
from django.db import models
from django.utils.translation import gettext_lazy as _
from core.helper.enums import ReportTypeChoice
from django.conf import settings
from core.helper.models import TimeBasedModel

import auto_prefetch

class Report(TimeBasedModel):
    """
    Pre-generated financial reports for admins.
    """
    report_type = models.CharField(max_length=50, choices=ReportTypeChoice.choices)
    file_path = models.CharField(max_length=255, blank=True, null=True)

    class Meta:
        verbose_name = _("Report")
        verbose_name_plural = _("Reports")
        ordering = ["-generated_at"]

    def __str__(self):
        return f"{self.report_type} - {self.generated_at}"



class AuditLog(TimeBasedModel):
    """
    Tracks admin/system activities for compliance and traceability.
    """

    ACTION_CHOICES = [
        ("create", "Create"),
        ("update", "Update"),
        ("delete", "Delete"),
        ("login", "Login"),
        ("logout", "Logout"),
    ]

    user = auto_prefetch.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_logs",
    )
    action = models.CharField(max_length=20, choices=ActionChoice.choices)
    module = models.CharField(max_length=100)
    description = models.TextField()
    ip_address = models.GenericIPAddressField(blank=True, null=True)

    class Meta:
        verbose_name = _("Audit Log")
        verbose_name_plural = _("Audit Logs")
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user} - {self.action} - {self.module}"