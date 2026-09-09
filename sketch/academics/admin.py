from django.contrib import admin

from core.applications.academics.models import ClassRoom, TeachingAssignment

# Register your models here.


@admin.register(ClassRoom)
class ClassRoomAdmin(admin.ModelAdmin):
    list_display = ("id", "school", "academic_class", "arm", "created_at", "updated_at")
    list_filter = ("school", "academic_class")
    search_fields = ("academic_class", "arm", "school__name")


@admin.register(TeachingAssignment)
class TeachingAssignmentAdmin(admin.ModelAdmin):
    list_display = ("id", "teacher", "classroom", "subject", "created_at", "updated_at")
    list_filter = ("classroom__school", "subject")
    search_fields = (
        "teacher__user__first_name",
        "teacher__user__last_name",
        "classroom__academic_class",
        "classroom__arm",
        "subject__name",
    )
