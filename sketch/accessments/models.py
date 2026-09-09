from django.db import models
from django.utils.translation import gettext_lazy as _
from django.core.exceptions import ValidationError

from core.helper.models import TimeStampedModel
import auto_prefetch


class AcademicSession(TimeStampedModel):
    """
    Represents a school academic year e.g. 2024/2025.
    """

    school = auto_prefetch.ForeignKey(
        "users.School", on_delete=models.CASCADE, related_name="academic_sessions"
    )
    name = models.CharField(
        max_length=20, help_text=_("Name of the session e.g. 2024/2025")
    )
    is_active = models.BooleanField(default=False)

    class Meta(auto_prefetch.Model.Meta):
        unique_together = ("school", "name")
        verbose_name = _("Academic Session")
        verbose_name_plural = _("Academic Sessions")

    def __str__(self):
        return self.name


class AcademicTerm(TimeStampedModel):
    """
    Represents a division of an academic session e.g. First Term.
    """

    session = auto_prefetch.ForeignKey(
        "accessments.AcademicSession", on_delete=models.CASCADE, related_name="terms"
    )
    name = models.CharField(max_length=50, help_text=_("1st Term, 2nd Term, 3rd Term"))
    is_active = models.BooleanField(default=False)
    term_type = models.CharField(
        max_length=20,
        choices=[
            ("HALF_TERM", "Half Term"),
            ("END_OF_TERM", "End of Term"),
            ("FULL_TERM", "Full Term"),
        ],
        default="FULL_TERM",
    )

    class Meta(auto_prefetch.Model.Meta):
        unique_together = ("session", "name")
        verbose_name = _("Academic Term")
        verbose_name_plural = _("Academic Terms")

    def __str__(self):
        return f"{self.name} - {self.session}"


class AssessmentPolicy(TimeStampedModel):
    """
    Defines configuration for grading continuous assessments per term.
    Example:
        - Tests (2 occurrences) → 40%
        - Exam (1 occurrence) → 60%
    """

    school = auto_prefetch.ForeignKey(
        "users.School", on_delete=models.CASCADE, related_name="assessment_policies"
    )
    term = auto_prefetch.ForeignKey(
        "accessments.AcademicTerm", on_delete=models.CASCADE, related_name="policies"
    )
    name = models.CharField(max_length=150, default="Default Policy")
    is_active = models.BooleanField(default=True)
    ca_weight = models.PositiveIntegerField(default=40, help_text=_("Continuous Assessment weight (%)"))
    exam_weight = models.PositiveIntegerField(default=60, help_text=_("Examination weight (%)"))

    class Meta(auto_prefetch.Model.Meta):
        constraints = [
            models.UniqueConstraint(
                fields=["school", "term", "is_active"],
                condition=models.Q(is_active=True),
                name="unique_active_policy_per_school_term",
            )
        ]
        verbose_name = _("Assessment Policy")
        verbose_name_plural = _("Assessment Policies")

    def __str__(self):
        return f"{self.school.name} - {self.term} ({self.name})"


class AssessmentType(TimeStampedModel):
    """
    A category of assessment that contributes to total grade.
    Example types: Test, Exam, Assignment, Project
    """

    policy = auto_prefetch.ForeignKey(
        "accessments.AssessmentPolicy",
        on_delete=models.CASCADE,
        related_name="assessment_types",
    )
    name = models.CharField(max_length=100)
    category = models.CharField(
        max_length=20,
        choices=[
            ("CA", "Continuous Assessment"),
            ("EXAM", "Examination"),
            ("HALF_TERM", "Half Term Exam"),
            ("PROJECT", "Project"),
            ("ASSIGNMENT", "Assignment"),
        ],
        default="CA",
    )
    count = models.PositiveIntegerField(default=1)
    weight = models.DecimalField(max_digits=5, decimal_places=2)
    max_score = models.PositiveIntegerField(default=100)
    is_optional = models.BooleanField(default=False)
    order = models.PositiveIntegerField(
        default=0, help_text=_("Display order in reports")
    )

    class Meta(auto_prefetch.Model.Meta):
        ordering = ["policy", "order", "name"]
        verbose_name = _("Assessment Type")
        verbose_name_plural = _("Assessment Types")

    def __str__(self):
        return f"{self.name} ({self.weight}%)"

    def clean(self):
        # Validate that weight doesn't exceed 100% when combined with other types in same policy
        if self.weight > 100:
            raise ValidationError(_("Weight cannot exceed 100%"))

        if self.policy:
            total_weight = (
                AssessmentType.objects.filter(policy=self.policy)
                .exclude(pk=self.pk)
                .aggregate(total=models.Sum("weight"))["total"]
                or 0
            )

            if total_weight + self.weight > 100:
                raise ValidationError(
                    _(
                        "Total weight for all assessment types in this policy would exceed 100%"
                    )
                )


class AssessmentRecord(TimeStampedModel):
    """
    Stores the actual recorded score per student per assessment instance.
    """

    student = auto_prefetch.ForeignKey(
        "users.StudentProfile",
        on_delete=models.CASCADE,
        related_name="assessment_records",
    )
    classroom_subject = auto_prefetch.ForeignKey(
        "timetable.Subject", on_delete=models.CASCADE, related_name="assessment_records"
    )
    assessment_type = auto_prefetch.ForeignKey(
        AssessmentType, on_delete=models.CASCADE, related_name="records"
    )
    index = models.PositiveIntegerField(help_text=_("Test/Exam number e.g. 1 or 2"))
    score = models.FloatField(null=True, blank=True)
    date_taken = models.DateField(null=True, blank=True)

    class Meta(auto_prefetch.Model.Meta):
        unique_together = ("student", "classroom_subject", "assessment_type", "index")
        verbose_name = _("Assessment Record")
        verbose_name_plural = _("Assessment Records")

    def __str__(self):
        return f"{self.student} - {self.assessment_type.name} {self.index}"

    @property
    def percentage_score(self):
        """Calculate percentage score based on max_score"""
        if self.score is None or self.assessment_type.max_score == 0:
            return 0
        return (self.score / self.assessment_type.max_score) * 100


class SubjectResult(TimeStampedModel):
    """
    Final subject performance for a student per term.
    """

    student = auto_prefetch.ForeignKey("users.StudentProfile", on_delete=models.CASCADE)
    classroom_subject = auto_prefetch.ForeignKey(
        "timetable.Subject", on_delete=models.CASCADE
    )
    term = auto_prefetch.ForeignKey(AcademicTerm, on_delete=models.CASCADE)

    total_ca = models.FloatField(default=0)
    exam_score = models.FloatField(default=0)
    half_term_score = models.FloatField(
        default=0, help_text=_("Score for half-term exams")
    )
    total_score = models.FloatField(default=0)
    average_score = models.FloatField(
        default=0, help_text=_("Average score for the subject")
    )

    grade = models.CharField(
        max_length=3, null=True, blank=True
    )  # Increased to 3 for A'
    grade_point = models.DecimalField(
        max_digits=3, decimal_places=1, null=True, blank=True
    )
    comment = models.CharField(max_length=255, null=True, blank=True)

    class Meta(auto_prefetch.Model.Meta):
        unique_together = ("student", "classroom_subject", "term")
        verbose_name = _("Subject Result")
        verbose_name_plural = _("Subject Results")

    def calculate_total_score(self):
        """Calculate total score based on assessment records and policy"""
        from django.db.models import Sum, Avg

        # Get all assessment records for this student, subject, and term
        records = AssessmentRecord.objects.filter(
            student=self.student,
            classroom_subject=self.classroom_subject,
            assessment_type__policy__term=self.term,
        ).select_related("assessment_type")

        total_score = 0

        for record in records:
            if record.score is not None:
                # Calculate weighted score
                percentage = (record.score / record.assessment_type.max_score) * 100
                weighted_score = (percentage * record.assessment_type.weight) / 100
                total_score += weighted_score

        return min(total_score, 100)  # Cap at 100%

    def save(self, *args, **kwargs):
        # Auto-calculate total score if not set
        if not self.total_score:
            self.total_score = self.calculate_total_score()

        # Calculate average (for half-term reports)
        ca_count = AssessmentRecord.objects.filter(
            student=self.student,
            classroom_subject=self.classroom_subject,
            assessment_type__policy__term=self.term,
            assessment_type__category="CA",
        ).count()

        if ca_count > 0:
            self.average_score = self.total_ca / ca_count if self.total_ca else 0

        super().save(*args, **kwargs)


class TeacherComment(TimeStampedModel):
    """
    Stores teacher comments for student performance in subjects.
    """

    subject_result = auto_prefetch.ForeignKey(
        SubjectResult, on_delete=models.CASCADE, related_name="teacher_comments"
    )
    teacher = auto_prefetch.ForeignKey("users.TeacherProfile", on_delete=models.CASCADE)
    comment = models.TextField()
    comment_type = models.CharField(
        max_length=20,
        choices=[
            ("STRENGTH", "Strength"),
            ("WEAKNESS", "Weakness"),
            ("GENERAL", "General"),
            ("IMPROVEMENT", "Area for Improvement"),
        ],
        default="GENERAL",
    )

    class Meta(auto_prefetch.Model.Meta):
        verbose_name = _("Teacher Comment")
        verbose_name_plural = _("Teacher Comments")
        ordering = ["subject_result", "comment_type"]

    def __str__(self):
        return f"{self.subject_result} - {self.comment_type}"


class TargetGrade(TimeStampedModel):
    """
    Stores target grades for students in subjects.
    """

    student = auto_prefetch.ForeignKey("users.StudentProfile", on_delete=models.CASCADE)
    classroom_subject = auto_prefetch.ForeignKey(
        "timetable.Subject", on_delete=models.CASCADE
    )
    term = auto_prefetch.ForeignKey(AcademicTerm, on_delete=models.CASCADE)
    target_grade = models.CharField(max_length=3)
    target_point = models.DecimalField(max_digits=3, decimal_places=1)
    current_grade = models.CharField(max_length=3, null=True, blank=True)
    current_point = models.DecimalField(
        max_digits=3, decimal_places=1, null=True, blank=True
    )

    class Meta(auto_prefetch.Model.Meta):
        unique_together = ("student", "classroom_subject", "term")
        verbose_name = _("Target Grade")
        verbose_name_plural = _("Target Grades")

    def __str__(self):
        return f"{self.student} - {self.classroom_subject}: {self.current_grade} → {self.target_grade}"


class TermReportSummary(TimeStampedModel):
    """
    Aggregates performance across all subjects in a term.
    """

    student = auto_prefetch.ForeignKey("users.StudentProfile", on_delete=models.CASCADE)
    term = auto_prefetch.ForeignKey(AcademicTerm, on_delete=models.CASCADE)
    class_group = auto_prefetch.ForeignKey(
        "academics.ClassRoom", on_delete=models.CASCADE, null=True, blank=True
    )

    total_score = models.FloatField(default=0)
    average_score = models.FloatField(default=0)
    total_points = models.FloatField(default=0)
    gpa = models.DecimalField(max_digits=3, decimal_places=2, null=True, blank=True)
    target_gpa = models.DecimalField(
        max_digits=3, decimal_places=2, null=True, blank=True
    )
    class_position = models.PositiveIntegerField(null=True, blank=True)
    attendance_percentage = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True
    )
    conduct_rating = models.CharField(max_length=20, null=True, blank=True)
    principal_comment = models.TextField(null=True, blank=True)
    form_teacher_comment = models.TextField(null=True, blank=True)

    class Meta(auto_prefetch.Model.Meta):
        unique_together = ("student", "term")
        verbose_name = _("Term Report Summary")
        verbose_name_plural = _("Term Report Summaries")

    def calculate_gpa(self):
        """Calculate GPA based on all subject results"""
        subject_results = SubjectResult.objects.filter(
            student=self.student, term=self.term
        )
        total_points = sum([sr.grade_point or 0 for sr in subject_results])
        count = subject_results.count()
        return total_points / count if count > 0 else 0

    def save(self, *args, **kwargs):
        # Auto-calculate GPA if not set
        if not self.gpa:
            self.gpa = self.calculate_gpa()

        # Calculate total points
        subject_results = SubjectResult.objects.filter(
            student=self.student, term=self.term
        )
        self.total_points = sum([sr.grade_point or 0 for sr in subject_results])

        super().save(*args, **kwargs)


class GradeScale(TimeStampedModel):
    """
    Defines grade brackets for a school (e.g., A = 75-100).
    """

    school = auto_prefetch.ForeignKey("users.School", on_delete=models.CASCADE)
    grade = models.CharField(max_length=3)
    display_name = models.CharField(
        max_length=5, null=True, blank=True, help_text=_("e.g., A' for honors")
    )
    min_score = models.PositiveIntegerField()
    max_score = models.PositiveIntegerField()
    point = models.DecimalField(max_digits=3, decimal_places=1)
    remark = models.CharField(max_length=255, null=True, blank=True)
    is_honors = models.BooleanField(
        default=False, help_text=_("Whether this is an honors grade (A')")
    )
    is_active = models.BooleanField(default=True)
    order = models.PositiveIntegerField(default=0)

    class Meta(auto_prefetch.Model.Meta):
        ordering = ["-max_score"]
        unique_together = ("school", "grade", "is_honors")
        verbose_name = _("Grade Scale")
        verbose_name_plural = _("Grade Scales")

    def __str__(self):
        display_name = self.display_name or self.grade
        return (
            f"{display_name}: {self.min_score}-{self.max_score} ({self.point} points)"
        )

    def clean(self):
        # Validate score range
        if self.min_score > self.max_score:
            raise ValidationError(_("Min score cannot be greater than max score"))

        if self.min_score < 0 or self.max_score > 100:
            raise ValidationError(_("Scores must be between 0 and 100"))
