from django.contrib import admin

from core.applications.notification.models import AuditLog

# Register your models here.


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "action", "organization", "actor_email", "resource_type", "resource_id")
    list_filter = ("action", "organization")
    search_fields = ("actor_email", "action", "resource_id", "organization__name")
    list_select_related = ("organization",)
    raw_id_fields = ("organization", "actor")

    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return False
