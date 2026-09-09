from django.db.models.signals import post_save
from django.dispatch import receiver
from django.db import transaction
from django.utils import timezone

from core.applications.accessments.models import (
    AssessmentRecord,
    SubjectResult,
    TermReportSummary,
    AcademicTerm,
    AssessmentPolicy
)
from core.helper.service import compute_subject_result



@receiver(post_save, sender=AssessmentRecord)
def update_subject_result(sender, instance, **kwargs):
    """
    Update SubjectResult when AssessmentRecords are saved
    """
    try:
        # Get the term from assessment type policy
        term = instance.assessment_type.policy.term

        subject_result, created = SubjectResult.objects.get_or_create(
            student=instance.student,
            classroom_subject=instance.classroom_subject,
            term=term,
            defaults={
                'total_ca': 0,
                'exam_score': 0,
                'total_score': 0,
            }
        )

        # Recalculate the subject result
        compute_subject_result(
            instance.student,
            instance.classroom_subject,
            term,
            recalc=True
        )

    except Exception as e:
        # Log error but don't crash the application
        print(f"Error updating subject result: {e}")


@receiver(post_save, sender=SubjectResult)
def update_term_report_summary(sender, instance, **kwargs):
    """
    Update TermReportSummary when SubjectResults are saved
    """
    try:
        # Get or create term report summary
        report_summary, created = TermReportSummary.objects.get_or_create(
            student=instance.student,
            term=instance.term,
            defaults={
                'total_score': 0,
                'average_score': 0,
                'total_points': 0,
                'gpa': 0,
            }
        )

        # Trigger recalculation by calling save which will recalculate GPA and position
        report_summary.save()

    except Exception as e:
        # Log error but don't crash the application
        print(f"Error updating term report summary: {e}")
