import auto_prefetch
from django.apps import apps
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

from core.applications.report.queryset import ReportQuerySet
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

    objects = ReportQuerySet.as_manager()

    class Meta(auto_prefetch.Model.Meta):
        verbose_name = _("Report")
        verbose_name_plural = _("Reports")
        ordering = ["-created_at"]

    def clean(self) -> None:
        super().clean()
        if self.generated_by_id and self.organization_id:
            Membership = apps.get_model("users", "Membership")
            is_member = (
                Membership.objects.effective()
                .for_user(self.generated_by)
                .filter(organization_id=self.organization_id)
                .exists()
            )
            if not is_member:
                raise ValidationError(
                    {"generated_by": _("Selected user does not belong to this organization.")}
                )

    def __str__(self) -> str:
        return f"{self.title or self.report_type} ({self.format}) - {self.status}"
