import json

from django.contrib import admin
from django.urls import NoReverseMatch
from django.urls import reverse
from django.utils.html import format_html

from core.applications.report.models import AuditLog
from core.applications.report.models import Report


@admin.register(Report)
class ReportAdmin(admin.ModelAdmin):
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
    def file_link(self, obj):
        if not obj.file:
            return "—"
        return format_html('<a href="{}" target="_blank">Download</a>', obj.file.url)

    def save_model(self, request, obj, form, change):
        # A report requested from admin is attributed to whoever requested
        # it — the actual file/status are filled in by the generation
        # pipeline, not here.
        if not change and not obj.generated_by_id:
            obj.generated_by = request.user
        super().save_model(request, obj, form, change)

    @admin.action(description="Reset selected reports to PENDING (for retry)")
    def reset_to_pending(self, request, queryset):
        updated = queryset.exclude(status="pending").update(status="pending", file=None)
        self.message_user(request, f"Reset {updated} report(s) to PENDING.")


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    """
    Read-only by design (PRD §30: "Audit logs are not editable by normal
    users"). This isn't scoped to "normal users" only — nothing in this
    admin can edit or delete a row, full stop. The only way a row exists
    is the application writing it at the moment the audited event
    happened.
    """

    list_display = (
        "created_at",
        "user",
        "action",
        "module",
        "linked_target",
        "organization",
        "ip_address",
    )
    list_filter = ("action", "module", "organization")
    search_fields = ("description", "module", "user__email", "ip_address")
    date_hierarchy = "created_at"
    list_select_related = ("user", "organization", "content_type")

    fieldsets = (
        (None, {"fields": ("organization", "user", "action", "module", "description", "ip_address", "created_at")}),
        ("Target record", {"fields": ("content_type", "object_id", "linked_target")}),
        ("Change detail", {"fields": ("previous_value_display", "new_value_display")}),
    )

    @admin.display(description="Target")
    def linked_target(self, obj):
        if not obj.content_type or not obj.object_id:
            return "—"
        label = f"{obj.content_type.name} #{obj.object_id}"
        try:
            url = reverse(
                f"admin:{obj.content_type.app_label}_{obj.content_type.model}_change",
                args=[obj.object_id],
            )
        except NoReverseMatch:
            return label
        return format_html('<a href="{}">{}</a>', url, label)

    @admin.display(description="Previous value")
    def previous_value_display(self, obj):
        return self._pretty_json(obj.previous_value)

    @admin.display(description="New value")
    def new_value_display(self, obj):
        return self._pretty_json(obj.new_value)

    @staticmethod
    def _pretty_json(value):
        if not value:
            return "—"
        return format_html("<pre>{}</pre>", json.dumps(value, indent=2, default=str))

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields] + [
            "linked_target",
            "previous_value_display",
            "new_value_display",
        ]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False