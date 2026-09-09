import auto_prefetch
from django.db import models
from django.utils.translation import gettext_lazy as _
from django.core.exceptions import ValidationError

from core.helper.enums import AcademicClass
from core.helper.enums import DayOfWeek
from core.helper.enums import UserRole
from core.helper.models import TimeStampedModel

class Subject(TimeStampedModel):
    school = auto_prefetch.ForeignKey(
        "users.School",
        on_delete=models.CASCADE,
        related_name="subjects",
    )
    name = models.CharField(max_length=100)
    code = models.CharField(max_length=20)
    description = models.TextField(blank=True, null=True)

    class_rooms = models.ManyToManyField(
        "academics.ClassRoom",
        related_name="subjects",
        blank=True,
    )

    is_active = models.BooleanField(default=True)

    class Meta(auto_prefetch.Model.Meta):
        unique_together = ("school", "code")
        ordering = ["name"]

    def __str__(self):
        return f"{self.code} - {self.name}"


class TimeSlot(TimeStampedModel):
    """
    Represents a specific period in a school day.
    Multi-tenant: each school has unique TimeSlots.
    """

    school = models.ForeignKey(
        "users.School",
        on_delete=models.CASCADE,
        related_name="time_slots",
    )

    name = models.CharField(max_length=50)
    start_time = models.TimeField()
    end_time = models.TimeField()
    is_break = models.BooleanField(default=False)
    order = models.PositiveIntegerField(default=0)

    class Meta(auto_prefetch.Model.Meta):
        db_table = "time_slots"
        verbose_name = _("Time Slot")
        verbose_name_plural = _("Time Slots")
        ordering = ["order", "start_time"]
        unique_together = ("school", "order")

    def __str__(self):
        return f"{self.name} ({self.start_time} - {self.end_time})"

class ClassSchedule(TimeStampedModel):
    """
    Represents a single class session for a school.
    Enforces strict multi-tenant ownership.
    """

    school = models.ForeignKey(
        "users.School",
        on_delete=models.CASCADE,
        related_name="class_schedules",

    )

    academic_class = models.CharField(max_length=50, choices=AcademicClass.choices)
    day_of_week = models.CharField(max_length=20, choices=DayOfWeek.choices)

    time_slot = auto_prefetch.ForeignKey(
        TimeSlot,
        on_delete=models.CASCADE,
        related_name="schedules",
    )
    subject = auto_prefetch.ForeignKey(
        Subject,
        on_delete=models.CASCADE,
        related_name="schedules",
        null=True,
        blank=True,
    )
    teacher = auto_prefetch.ForeignKey(
        "users.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        limit_choices_to={"role": UserRole.TEACHER},
        related_name="teaching_schedules",
    )
    room_number = models.CharField(max_length=50, blank=True, null=True)
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True, null=True)

    class Meta(auto_prefetch.Model.Meta):
        db_table = "class_schedules"
        verbose_name = _("Class Schedule")
        verbose_name_plural = _("Class Schedules")
        ordering = ["academic_class", "day_of_week", "time_slot__order"]
        unique_together = [
            ["school", "academic_class", "day_of_week", "time_slot"],
        ]

    def __str__(self):
        subject_name = self.subject.name if self.subject else "Break"
        return f"{self.academic_class} - {self.day_of_week} - {subject_name}"
    # 🔒 MULTI-TENANT VALIDATION
    def clean(self):
        # 1. Teacher must belong to the same school
        if self.teacher and self.teacher.school != self.school:
            raise ValidationError(_("Teacher must belong to the same school."))

        # 2. Foreign keys must match same school
        if self.time_slot.school != self.school:
            raise ValidationError(_("Time slot does not belong to this school."))

        if self.subject and self.subject.school != self.school:
            raise ValidationError(_("Subject does not belong to this school."))

        # 3. Teacher role
        if self.teacher and self.teacher.role != UserRole.TEACHER:
            raise ValidationError(_("Only teachers can be assigned."))

        # 4. Break period rules
        if self.time_slot.is_break and (self.subject or self.teacher):
            raise ValidationError(_("Break periods cannot have subjects or teachers."))

        if not self.time_slot.is_break and not self.subject:
            raise ValidationError(_("Non-break periods must have a subject."))
            raise ValidationError(_("Non-break periods must have a subject."))

class Timetable(TimeStampedModel):
    """
    Represents a complete timetable for a specific school.
    """

    school = models.ForeignKey(
        "users.School",
        on_delete=models.CASCADE,
        related_name="timetables",
    )

    name = models.CharField(max_length=100)
    academic_year = models.CharField(max_length=20)
    term = models.CharField(max_length=50)
    start_date = models.DateField()
    end_date = models.DateField()
    is_active = models.BooleanField(default=True)

    schedules = models.ManyToManyField(
        ClassSchedule,
        related_name="timetables",
    )

    class Meta(auto_prefetch.Model.Meta):
        db_table = "timetables"
        verbose_name = _("Timetable")
        verbose_name_plural = _("Timetables")
        ordering = ["-start_date"]
        unique_together = ("school", "name", "academic_year", "term")

    def __str__(self):
        return f"{self.name} ({self.academic_year})"

    #  MULTI-TENANT SAFETY
    def clean(self):
        for schedule in self.schedules.all():
            if schedule.school != self.school:
                raise ValidationError(
                    _("All schedules must belong to the same school.")
                )

    def save(self, *args, **kwargs):
        # Only one active timetable PER SCHOOL
        if self.is_active:
            Timetable.objects.filter(
                school=self.school,
                is_active=True
            ).exclude(pk=self.pk).update(is_active=False)

        super().save(*args, **kwargs)
