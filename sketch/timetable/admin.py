
from django.contrib import admin

from core.applications.timetable.models import Subject



# Register your models here.
@admin.register(Subject)
class SubjectAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "code", "school", "created_at", "updated_at")
    list_filter = ("school",)
    search_fields = ("name", "code", "school__name")
