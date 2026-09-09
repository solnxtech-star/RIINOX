import auto_prefetch
from django.db import models
from django.utils.translation import gettext_lazy as _
from django.core.exceptions import ValidationError

from core.helper.enums import AcademicClass
from core.helper.models import TimeStampedModel


# Create your models here.
class ClassRoom(TimeStampedModel):
    school = auto_prefetch.ForeignKey(
        "users.School",
        on_delete=models.CASCADE,
        related_name="classrooms",
    )

    academic_class = models.CharField(
        max_length=20,
        choices=AcademicClass.choices,
        help_text=_("Parent academic class (JSS1, SS2 etc.)"),
    )

    arm = models.CharField(
        max_length=10,
        help_text=_("Class arm e.g. A, B, C"),
    )

    class Meta(auto_prefetch.Model.Meta):
        unique_together = ("school", "academic_class", "arm")
        ordering = ["academic_class", "arm"]

    def __str__(self):
        return f"{self.academic_class} {self.arm}"


class TeachingAssignment(TimeStampedModel):
    """
    Defines what a teacher teaches:
    - Which class(es)
    - Which subject(s)
    Fully multi-tenant and scalable.
    """

    teacher = auto_prefetch.ForeignKey(
        "users.TeacherProfile",
        on_delete=models.CASCADE,
        related_name="teaching_assignments",
    )

    classroom = auto_prefetch.ForeignKey(
        "academics.ClassRoom",
        on_delete=models.CASCADE,
        related_name="teaching_assignments",
    )

    subject = auto_prefetch.ForeignKey(
        "timetable.Subject",
        on_delete=models.CASCADE,
        related_name="teaching_assignments",
    )

    class Meta(auto_prefetch.Model.Meta):
        db_table = "teaching_assignments"
        verbose_name = "Teaching Assignment"
        verbose_name_plural = "Teaching Assignments"
        unique_together = ("teacher", "classroom", "subject")

    def clean(self):
        """
        SaaS MULTI-TENANT VALIDATION:
        Ensure teacher, classroom, and subject belong to the same school.
        """
        if (
            self.teacher.school != self.classroom.school
            or self.teacher.school != self.subject.school
        ):
            raise ValidationError(
                "Teacher, Classroom, and Subject must belong to the same school."
            )


    def save(self, *args, **kwargs):
        # Runs clean() **every time**, including bulk or manual create
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.teacher.user.name} teaches {self.subject.name} in {self.classroom}"
