import auto_prefetch
from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils.translation import gettext_lazy as _

from core.helper.enums import AuditActionChoices
from core.helper.enums import ReportFormatChoices
from core.helper.enums import ReportStatusChoices
from core.helper.enums import ReportTypeChoice
from core.helper.media import MediaHelper
from core.helper.models import TimeBasedModel


class Report(TimeBasedModel):
    """
    Represents a system-generated report stored in the platform (PRD §29).

    This model is generic and reusable across multiple modules:
      - Financial reports (revenue, transactions, balances)
      - Inventory reports (stock levels, shortages, restocks)
      - Sales reports (daily/weekly summaries, top products)
      - Compliance/audit reports (logs, activity tracking)
      - Custom analytical reports
    """

    organization = auto_prefetch.ForeignKey(
        "users.Organization",
        on_delete=models.CASCADE,
        related_name="reports",
    )
    report_type = models.CharField(
        max_length=50,
        choices=ReportTypeChoice.choices,
        help_text=_(
            "The category/type of this report (finance, inventory, sales, etc.).",
        ),
    )
    title = models.CharField(
        max_length=150,
        help_text=_("A short descriptive title for the report."),
    )
    description = models.TextField(
        blank=True,
        null=True,
        help_text=_("Detailed information about the report."),
    )
    filters = models.JSONField(
        default=dict,
        blank=True,
        help_text=_("Parameters used to generate this report (date range, staff, product, etc.) for reproducibility."),
    )
    file = models.FileField(
        upload_to=MediaHelper.get_image_upload_path,
        blank=True,
        null=True,
        help_text=_("The generated report file."),
    )
    format = models.CharField(
        max_length=10,
        choices=ReportFormatChoices.choices,
        default=ReportFormatChoices.PDF,
        help_text=_("The file format of the report."),
    )
    status = models.CharField(
        max_length=20,
        choices=ReportStatusChoices.choices,
        default=ReportStatusChoices.PENDING,
        help_text=_("The current status of report generation."),
    )
    generated_by = auto_prefetch.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="generated_reports",
        help_text=_("The user/admin who generated this report."),
    )

    class Meta(auto_prefetch.Model.Meta):
        verbose_name = _("Report")
        verbose_name_plural = _("Reports")
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.title or self.report_type} ({self.format}) - {self.status}"


class AuditLog(TimeBasedModel):
    """
    Tracks system and administrative activities for compliance, security,
    and traceability (PRD §30).

    `content_type`/`object_id`/`target` point at exactly which record
    changed; `previous_value`/`new_value` capture what changed. Without
    these two additions the model could log *that* something happened
    but not *what* — which is precisely what §30 asks for. This is
    intentionally immutable-in-spirit: nothing in the application should
    ever update an existing AuditLog row, only create new ones.
    """

    organization = auto_prefetch.ForeignKey(
        "users.Organization",
        on_delete=models.CASCADE,
        related_name="audit_logs",
    )
    user = auto_prefetch.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_logs",
    )
    action = models.CharField(max_length=20, choices=AuditActionChoices.choices)
    module = models.CharField(max_length=100)
    description = models.TextField()
    content_type = auto_prefetch.ForeignKey(
        ContentType,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    object_id = models.PositiveIntegerField(null=True, blank=True)
    target = GenericForeignKey("content_type", "object_id")
    previous_value = models.JSONField(
        null=True,
        blank=True,
        help_text=_("Snapshot of the relevant field(s) before the change."),
    )
    new_value = models.JSONField(
        null=True,
        blank=True,
        help_text=_("Snapshot of the relevant field(s) after the change."),
    )
    ip_address = models.GenericIPAddressField(blank=True, null=True)

    class Meta(auto_prefetch.Model.Meta):
        verbose_name = _("Audit Log")
        verbose_name_plural = _("Audit Logs")
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["content_type", "object_id"])]

    def __str__(self):
        return f"{self.user} - {self.action} - {self.module}"