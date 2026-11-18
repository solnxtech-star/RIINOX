from django import forms
from django.contrib import admin
from django.utils.html import format_html

from core.applications.report.models import Report


class ReportAdminForm(forms.ModelForm):
    class Meta:
        model = Report
        fields = "__all__"

    def clean(self):
        cleaned_data = super().clean()
        self.instance.organization = cleaned_data.get("organization")
        self.instance.generated_by = cleaned_data.get("generated_by")
        self.instance.clean()
        return cleaned_data


@admin.register(Report)
class ReportAdmin(admin.ModelAdmin):
    form = ReportAdminForm
    list_display = (
        "title",
        "report_type",
        "format",
        "status",
        "organization",
        "generated_by",
        "created_at",
        "file_link",
    )
    list_filter = ("report_type", "format", "status", "organization")
    search_fields = ("title", "description")
    list_select_related = ("organization", "generated_by")
    readonly_fields = ("file", "status", "generated_by", "created_at", "updated_at", "file_link")
    actions = ["reset_to_pending"]

    fieldsets = (
        (None, {"fields": ("organization", "title", "report_type", "description")}),
        ("Parameters", {"fields": ("filters",)}),
        ("Generation", {"fields": ("status", "format", "file", "file_link", "generated_by")}),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )

    @admin.display(description="File")
    def file_link(self, report_record):
        if not report_record.file:
            return "—"
        return format_html('<a href="{}" target="_blank">Download</a>', report_record.file.url)

    def save_model(self, request, report_record, form, change):
        # A report requested from admin is attributed to whoever requested
        # it — the actual file/status are filled in by the generation
        # pipeline, not here.
        if not change and not report_record.generated_by_id:
            report_record.generated_by = request.user
        super().save_model(request, report_record, form, change)

    @admin.action(description="Reset selected reports to PENDING (for retry)")
    def reset_to_pending(self, request, queryset):
        updated = queryset.exclude(status="pending").update(status="pending", file=None)
        self.message_user(request, f"Reset {updated} report(s) to PENDING.")
