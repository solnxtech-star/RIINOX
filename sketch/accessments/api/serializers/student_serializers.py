# core/applications/accessments/serializers/student_views.py
from rest_framework import serializers
from django.utils.translation import gettext_lazy as _
from core.applications.accessments.models import (
    SubjectResult, TermReportSummary, AcademicTerm
)
from core.applications.users.models import StudentProfile


class StudentDashboardSerializer(serializers.ModelSerializer):
    """
    Serializer for student dashboard data.
    Provides overview of student's current performance.

    Example Output:
        {
            "student_info": {...},
            "current_term": {...},
            "recent_results": [...],
            "upcoming_assessments": [...],
            "performance_summary": {...}
        }
    """

    student_info = serializers.SerializerMethodField(
        help_text=_("Basic student information")
    )
    current_term = serializers.SerializerMethodField(
        help_text=_("Current academic term information")
    )
    recent_results = serializers.SerializerMethodField(
        help_text=_("Recent subject results")
    )
    attendance_summary = serializers.SerializerMethodField(
        help_text=_("Attendance summary for current term")
    )
    performance_summary = serializers.SerializerMethodField(
        help_text=_("Overall performance summary")
    )

    class Meta:
        model = StudentProfile
        fields = [
            'student_info', 'current_term', 'recent_results',
            'attendance_summary', 'performance_summary'
        ]

    def get_student_info(self, obj):
        """
        Get student information for dashboard.

        Args:
            obj (StudentProfile): The student profile instance

        Returns:
            dict: Student information
        """
        return {
            'name': obj.get_full_name(),
            'student_id': obj.student_id,
            'class_room': f"{obj.class_room.academic_class} {obj.class_room.arm}" if obj.class_room else None,
            'profile_picture': obj.profile_picture.url if obj.profile_picture else None
        }

    def get_current_term(self, obj):
        """
        Get current academic term information.

        Args:
            obj (StudentProfile): The student profile instance

        Returns:
            dict: Current term information or None
        """
        current_term = AcademicTerm.objects.filter(
            session__school=obj.school,
            session__is_active=True,
            is_active=True
        ).first()

        if current_term:
            return {
                'id': current_term.id,
                'name': current_term.name,
                'term_type': current_term.get_term_type_display(),
                'session': current_term.session.name
            }
        return None

    def get_recent_results(self, obj):
        """
        Get recent subject results for dashboard.

        Args:
            obj (StudentProfile): The student profile instance

        Returns:
            list: List of recent subject results
        """
        current_term = self.get_current_term(obj)['id'] if self.get_current_term(obj) else None

        if current_term:
            results = SubjectResult.objects.filter(
                student=obj,
                term_id=current_term
            ).select_related(
                'classroom_subject__subject'
            ).order_by('-total_score')[:5]  # Top 5 subjects by score

            return [
                {
                    'subject': result.classroom_subject.subject.name,
                    'score': result.total_score,
                    'grade': result.grade,
                    'grade_point': result.grade_point
                }
                for result in results
            ]
        return []

    def get_attendance_summary(self, obj):
        """
        Get attendance summary for current term.

        Args:
            obj (StudentProfile): The student profile instance

        Returns:
            dict: Attendance summary or None
        """
        current_term = self.get_current_term(obj)['id'] if self.get_current_term(obj) else None

        if current_term:
            # Get term report summary for attendance
            summary = TermReportSummary.objects.filter(
                student=obj,
                term_id=current_term
            ).first()

            if summary and summary.attendance_percentage:
                return {
                    'percentage': summary.attendance_percentage,
                    'rating': self._get_attendance_rating(summary.attendance_percentage)
                }

        return None

    def get_performance_summary(self, obj):
        """
        Get overall performance summary.

        Args:
            obj (StudentProfile): The student profile instance

        Returns:
            dict: Performance summary
        """
        # Get current term summary
        current_term = self.get_current_term(obj)['id'] if self.get_current_term(obj) else None

        if current_term:
            summary = TermReportSummary.objects.filter(
                student=obj,
                term_id=current_term
            ).first()

            if summary:
                return {
                    'gpa': summary.gpa,
                    'average_score': summary.average_score,
                    'class_position': summary.class_position,
                    'conduct_rating': summary.conduct_rating
                }

        # Fallback to latest term summary
        latest_summary = TermReportSummary.objects.filter(
            student=obj
        ).order_by('-term__session__name', '-term__name').first()

        if latest_summary:
            return {
                'gpa': latest_summary.gpa,
                'average_score': latest_summary.average_score,
                'class_position': latest_summary.class_position,
                'conduct_rating': latest_summary.conduct_rating,
                'term': latest_summary.term.name,
                'session': latest_summary.term.session.name
            }

        return {}

    def _get_attendance_rating(self, percentage):
        """
        Convert attendance percentage to rating.

        Args:
            percentage (float): Attendance percentage

        Returns:
            str: Attendance rating
        """
        if percentage >= 95:
            return "Excellent"
        elif percentage >= 90:
            return "Very Good"
        elif percentage >= 85:
            return "Good"
        elif percentage >= 80:
            return "Satisfactory"
        else:
            return "Needs Improvement"


class StudentProgressTrackerSerializer(serializers.Serializer):
    """
    Serializer for student progress tracking across terms.
    Shows term-by-term performance comparison.

    Example Output:
        {
            "student_info": {...},
            "term_performance": [...],
            "subject_trends": [...],
            "target_vs_actual": {...}
        }
    """

    student_id = serializers.IntegerField(
        required=True,
        help_text=_("ID of the student to track progress for")
    )
    start_term_id = serializers.IntegerField(
        required=False,
        help_text=_("ID of the starting term for tracking")
    )
    end_term_id = serializers.IntegerField(
        required=False,
        help_text=_("ID of the ending term for tracking")
    )

    def validate(self, data):
        """
        Validate progress tracking request.

        Args:
            data (dict): The progress tracking request data

        Returns:
            dict: Validated data

        Raises:
            serializers.ValidationError: If validation fails
        """
        request = self.context['request']
        student_id = data['student_id']

        # Verify student exists and belongs to user's school
        try:
            student = StudentProfile.objects.get(
                id=student_id,
                school=request.user.school
            )
        except StudentProfile.DoesNotExist:
            raise serializers.ValidationError({
                'student_id': _("Student not found in your school")
            })

        data['student'] = student
        return data


class TermPerformanceSerializer(serializers.Serializer):
    """
    Serializer for term performance data in progress tracking.
    """

    term_id = serializers.IntegerField(
        help_text=_("Academic term ID")
    )
    term_name = serializers.CharField(
        help_text=_("Term name (e.g., '1st Term')")
    )
    session_name = serializers.CharField(
        help_text=_("Academic session name (e.g., '2024/2025')")
    )
    gpa = serializers.FloatField(
        help_text=_("Grade Point Average for the term")
    )
    average_score = serializers.FloatField(
        help_text=_("Average score across all subjects")
    )
    class_position = serializers.IntegerField(
        allow_null=True,
        help_text=_("Position in class for the term")
    )
    total_subjects = serializers.IntegerField(
        help_text=_("Total number of subjects taken")
    )
    subjects_passed = serializers.IntegerField(
        help_text=_("Number of subjects passed")
    )
    attendance_percentage = serializers.FloatField(
        allow_null=True,
        help_text=_("Attendance percentage for the term")
    )


class SubjectTrendSerializer(serializers.Serializer):
    """
    Serializer for subject performance trends across terms.
    """

    subject_name = serializers.CharField(
        help_text=_("Name of the subject")
    )
    subject_code = serializers.CharField(
        help_text=_("Subject code")
    )
    term_performance = serializers.ListField(
        child=serializers.DictField(),
        help_text=_("Performance data for each term")
    )
    average_score = serializers.FloatField(
        help_text=_("Average score across all tracked terms")
    )
    trend = serializers.CharField(
        help_text=_("Performance trend: 'improving', 'declining', or 'stable'")
    )


class ProgressTrackingResponseSerializer(serializers.Serializer):
    """
    Serializer for progress tracking response.
    """

    student_info = serializers.DictField(
        help_text=_("Student information")
    )
    term_performance = TermPerformanceSerializer(
        many=True,
        help_text=_("Performance data for each term")
    )
    subject_trends = SubjectTrendSerializer(
        many=True,
        help_text=_("Performance trends for each subject")
    )
    target_vs_actual = serializers.DictField(
        help_text=_("Comparison of target vs actual performance")
    )
    summary = serializers.DictField(
        help_text=_("Overall progress summary")
    )
