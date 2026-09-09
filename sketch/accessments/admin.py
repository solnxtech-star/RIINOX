from django.contrib import admin

from core.applications.accessments.models import AcademicSession, AssessmentType, AssessmentPolicy

# Register your models here.

@admin.register(AcademicSession)
class AcademicSessionAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "school", "is_active", "created_at", "updated_at")
    list_filter = ("school", "is_active")
    search_fields = ("name", "school__name")

@admin.register(AssessmentType)
class AssessmentTypeAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "created_at", "updated_at")
    # list_filter = ("school",)
    search_fields = ("name",)



@admin.register(AssessmentPolicy)
class AssessmentPolicyAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "school", "created_at", "updated_at")
    list_filter = ("school",)
    search_fields = ("name", "school__name")
