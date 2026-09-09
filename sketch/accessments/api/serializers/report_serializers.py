from rest_framework import serializers
from django.utils.translation import gettext_lazy as _
from core.applications.accessments.models import (
    SubjectResult, TermReportSummary, AcademicTerm, GradeScale
)
from core.applications.timetable.models import ClassRoom
from core.applications.users.models import StudentProfile


class StudentReportDataSerializer(serializers.Serializer):
    """
    Serializer for generating student report data.
    Combines subject results, term summary, and additional information
    needed for report generation.

    Example Output:
        {
            "student_info": {...},
            "subject_results": [...],
            "term_summary": {...},
            "grading_key": [...],
            "report_metadata": {...}
        }
    """

    student_id = serializers.IntegerField(
        required=True,
        help_text=_("ID of the student to generate report for")
    )
    term_id = serializers.IntegerField(
        required=True,
        help_text=_("ID of the academic term for the report")
    )
    include_comments = serializers.BooleanField(
        default=True,
        help_text=_("Include teacher comments in the report")
    )
    include_targets = serializers.BooleanField(
        default=True,
        help_text=_("Include target grades comparison in the report")
    )
    report_type = serializers.ChoiceField(
        choices=[
            ('end_term', _('End of Term Report')),
            ('half_term', _('Half Term Progress Report')),
            ('transcript', _('Academic Transcript')),
            ('individual', _('Individual Subject Report'))
        ],
        default='end_term',
        help_text=_("Type of report to generate")
    )

    def validate(self, data):
        """
        Validate report generation request.

        Ensures:
        1. Student exists and belongs to user's school
        2. Term exists and belongs to user's school
        3. Student is enrolled in the selected term

        Args:
            data (dict): The report generation request data

        Returns:
            dict: Validated data with student and term instances

        Raises:
            serializers.ValidationError: If validation fails
        """
        request = self.context['request']
        school = request.user.school

        # Get student
        try:
            student = StudentProfile.objects.get(
                id=data['student_id'],
                school=school
            )
        except StudentProfile.DoesNotExist:
            raise serializers.ValidationError({
                'student_id': _("Student not found in your school")
            })

        # Get academic term
        try:
            term = AcademicTerm.objects.get(
                id=data['term_id'],
                session__school=school
            )
        except AcademicTerm.DoesNotExist:
            raise serializers.ValidationError({
                'term_id': _("Academic term not found in your school")
            })

        # Check if student has results for this term
        if not SubjectResult.objects.filter(
            student=student,
            term=term
        ).exists():
            raise serializers.ValidationError({
                'student_id': _("No results found for this student in the selected term")
            })

        data['student'] = student
        data['term'] = term

        return data


class SubjectResultReportSerializer(serializers.ModelSerializer):
    """
    Serializer for subject results in report format.
    Includes all information needed for displaying on report sheets.

    Example Output:
        {
            "subject_name": "Mathematics",
            "subject_code": "MATH",
            "total_ca": 38.0,
            "exam_score": 52.0,
            "total_score": 90.0,
            "grade": "A",
            "grade_point": 5.0,
            "remark": "Excellent",
            "teacher_comments": [...],
            "target_grade": "A",
            "target_point": 5.0
        }
    """

    subject_name = serializers.CharField(
        source='classroom_subject.subject.name',
        read_only=True,
        help_text=_("Name of the subject")
    )
    subject_code = serializers.CharField(
        source='classroom_subject.subject.code',
        read_only=True,
        help_text=_("Subject code")
    )
    teacher_comments = serializers.SerializerMethodField(
        help_text=_("Teacher comments for this subject")
    )

    class Meta:
        model = SubjectResult
        fields = [
            'subject_name', 'subject_code', 'total_ca', 'exam_score',
            'half_term_score', 'total_score', 'average_score', 'grade',
            'grade_point', 'remark', 'teacher_comments', 'target_grade',
            'target_point', 'subject_position'
        ]

    def get_teacher_comments(self, obj):
        """
        Get teacher comments for this subject result.

        Args:
            obj (SubjectResult): The subject result instance

        Returns:
            list: List of teacher comments with type and text
        """
        from core.applications.accessments.models import TeacherComment

        comments = TeacherComment.objects.filter(
            subject_result=obj,
            is_visible_to_parents=True
        ).order_by('comment_type')

        return [
            {
                'type': comment.get_comment_type_display(),
                'comment': comment.comment,
                'teacher': comment.teacher.get_full_name() if comment.teacher else None
            }
            for comment in comments
        ]


class TermReportSummarySerializer(serializers.ModelSerializer):
    """
    Serializer for term report summary in report format.
    Includes overall performance metrics for the term.

    Example Output:
        {
            "total_score": 450.0,
            "average_score": 75.0,
            "total_points": 20.0,
            "gpa": 4.0,
            "target_gpa": 4.5,
            "class_position": 5,
            "attendance_percentage": 95.5,
            "conduct_rating": "Excellent"
        }
    """

    student_name = serializers.CharField(
        source='student.get_full_name',
        read_only=True,
        help_text=_("Student's full name")
    )
    student_id = serializers.CharField(
        source='student.student_id',
        read_only=True,
        help_text=_("Student's unique ID")
    )
    class_name = serializers.SerializerMethodField(
        help_text=_("Formatted class name (e.g., 'SS2 A')")
    )

    class Meta:
        model = TermReportSummary
        fields = [
            'student_name', 'student_id', 'class_name', 'total_score',
            'average_score', 'total_points', 'gpa', 'target_gpa',
            'target_total_points', 'class_position', 'attendance_percentage',
            'conduct_rating', 'principal_comment', 'form_teacher_comment'
        ]

    def get_class_name(self, obj):
        """
        Get formatted class name.

        Args:
            obj (TermReportSummary): The term report summary instance

        Returns:
            str: Formatted class name (e.g., "SS2 A")
        """
        if obj.student.class_room:
            return f"{obj.student.class_room.academic_class} {obj.student.class_room.arm}"
        return ""


class BulkReportGenerationSerializer(serializers.Serializer):
    """
    Serializer for bulk report generation.
    Allows generating reports for multiple students/classes at once.

    Example:
        {
            "term_id": 1,
            "class_room_ids": [1, 2, 3],
            "report_type": "end_term",
            "format": "pdf",
            "include_summary": true
        }
    """

    term_id = serializers.IntegerField(
        required=True,
        help_text=_("ID of the academic term")
    )
    class_room_ids = serializers.ListField(
        child=serializers.IntegerField(),
        required=False,
        help_text=_("List of classroom IDs to generate reports for (all if empty)")
    )
    student_ids = serializers.ListField(
        child=serializers.IntegerField(),
        required=False,
        help_text=_("List of specific student IDs to generate reports for")
    )
    report_type = serializers.ChoiceField(
        choices=[
            ('end_term', _('End of Term Report')),
            ('half_term', _('Half Term Progress Report'))
        ],
        default='end_term',
        help_text=_("Type of report to generate")
    )
    format = serializers.ChoiceField(
        choices=[('pdf', 'PDF'), ('html', 'HTML'), ('json', 'JSON')],
        default='pdf',
        help_text=_("Output format for the reports")
    )
    include_summary = serializers.BooleanField(
        default=True,
        help_text=_("Include term summary page in the report")
    )
    include_grading_key = serializers.BooleanField(
        default=True,
        help_text=_("Include grading key/scale in the report")
    )

    def validate(self, data):
        """
        Validate bulk report generation request.

        Ensures:
        1. Term exists and belongs to user's school
        2. Classrooms exist and belong to user's school (if specified)
        3. Students exist and belong to user's school (if specified)
        4. At least one of class_room_ids or student_ids is provided

        Args:
            data (dict): The bulk report generation data

        Returns:
            dict: Validated data

        Raises:
            serializers.ValidationError: If validation fails
        """
        request = self.context['request']
        school = request.user.school

        # Get academic term
        try:
            term = AcademicTerm.objects.get(
                id=data['term_id'],
                session__school=school
            )
        except AcademicTerm.DoesNotExist:
            raise serializers.ValidationError({
                'term_id': _("Academic term not found in your school")
            })

        # Validate classrooms if provided
        class_room_ids = data.get('class_room_ids', [])
        if class_room_ids:
            valid_classrooms = ClassRoom.objects.filter(
                id__in=class_room_ids,
                school=school
            ).values_list('id', flat=True)

            invalid_classrooms = set(class_room_ids) - set(valid_classrooms)
            if invalid_classrooms:
                raise serializers.ValidationError({
                    'class_room_ids': _(
                        "The following classrooms were not found: %(classrooms)s"
                    ) % {'classrooms': ', '.join(map(str, invalid_classrooms))}
                })

        # Validate students if provided
        student_ids = data.get('student_ids', [])
        if student_ids:
            valid_students = StudentProfile.objects.filter(
                id__in=student_ids,
                school=school
            ).values_list('id', flat=True)

            invalid_students = set(student_ids) - set(valid_students)
            if invalid_students:
                raise serializers.ValidationError({
                    'student_ids': _(
                        "The following students were not found: %(students)s"
                    ) % {'students': ', '.join(map(str, invalid_students))}
                })

        # Ensure at least one filter is provided
        if not class_room_ids and not student_ids:
            raise serializers.ValidationError({
                'non_field_errors': _(
                    "Please specify either class_room_ids or student_ids"
                )
            })

        data['term'] = term
        return data


class GradingKeySerializer(serializers.ModelSerializer):
    """
    Serializer for grading key/scale for inclusion in reports.
    Shows the school's grading system at a glance.

    Example Output:
        [
            {"grade": "A", "min_score": 75, "max_score": 100, "point": 5.0},
            {"grade": "B", "min_score": 70, "max_score": 74, "point": 4.0},
            ...
        ]
    """

    score_range = serializers.SerializerMethodField(
        help_text=_("Formatted score range (e.g., '75-100')")
    )

    class Meta:
        model = GradeScale
        fields = ['grade', 'display_name', 'score_range', 'point', 'remark']

    def get_score_range(self, obj):
        """
        Format score range for display.

        Args:
            obj (GradeScale): The grade scale instance

        Returns:
            str: Formatted score range
        """
        return f"{obj.min_score}-{obj.max_score}"


class ReportPreviewSerializer(serializers.Serializer):
    """
    Serializer for report preview data.
    Returns structured data ready for template rendering.

    Example Output:
        {
            "school_info": {...},
            "student_info": {...},
            "report_period": {...},
            "subject_results": [...],
            "term_summary": {...},
            "grading_key": [...],
            "teacher_comments": [...],
            "signatures": {...}
        }
    """

    school_info = serializers.DictField(
        help_text=_("School information (name, address, contact, logo)")
    )
    student_info = serializers.DictField(
        help_text=_("Student information (name, ID, class, etc.)")
    )
    report_period = serializers.DictField(
        help_text=_("Report period information (term, session, date)")
    )
    subject_results = SubjectResultReportSerializer(
        many=True,
        help_text=_("List of subject results")
    )
    term_summary = TermReportSummarySerializer(
        help_text=_("Term summary and overall performance")
    )
    grading_key = GradingKeySerializer(
        many=True,
        required=False,
        help_text=_("School's grading system for reference")
    )
    teacher_comments = serializers.ListField(
        child=serializers.DictField(),
        required=False,
        help_text=_("Consolidated teacher comments")
    )
    signatures = serializers.DictField(
        required=False,
        help_text=_("Signatures section (Principal, Form Teacher, etc.)")
    )
    report_metadata = serializers.DictField(
        required=False,
        help_text=_("Report metadata (generated date, version, etc.)")
    )


class ReportStatusSerializer(serializers.Serializer):
    """
    Serializer for report generation status tracking.
    Used for async report generation to track progress.

    Example Output:
        {
            "task_id": "abc123",
            "status": "processing",
            "progress": 75,
            "total_reports": 14,
            "completed_reports": 10,
            "estimated_time": 30,
            "download_url": "/api/reports/download/abc123/",
            "error_message": null
        }
    """

    task_id = serializers.CharField(
        required=True,
        help_text=_("Unique identifier for the report generation task")
    )
    status = serializers.ChoiceField(
        choices=[
            ('pending', _('Pending')),
            ('processing', _('Processing')),
            ('completed', _('Completed')),
            ('failed', _('Failed'))
        ],
        help_text=_("Current status of the report generation")
    )
    progress = serializers.IntegerField(
        min_value=0,
        max_value=100,
        help_text=_("Percentage completion (0-100)")
    )
    total_reports = serializers.IntegerField(
        min_value=0,
        required=False,
        help_text=_("Total number of reports to generate")
    )
    completed_reports = serializers.IntegerField(
        min_value=0,
        required=False,
        help_text=_("Number of reports completed")
    )
    estimated_time = serializers.IntegerField(
        min_value=0,
        required=False,
        help_text=_("Estimated time remaining in seconds")
    )
    download_url = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text=_("URL to download generated reports")
    )
    error_message = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
        help_text=_("Error message if generation failed")
    )

    def validate(self, data):
        """
        Validate report status data.

        Ensures:
        1. Progress is consistent with status
        2. Completed reports <= total reports
        3. Download URL only provided when completed

        Args:
            data (dict): The report status data

        Returns:
            dict: Validated data

        Raises:
            serializers.ValidationError: If validation fails
        """
        status = data.get('status')
        progress = data.get('progress', 0)

        # Validate progress based on status
        if status == 'completed' and progress < 100:
            raise serializers.ValidationError({
                'progress': _("Progress must be 100% when status is 'completed'")
            })

        if status == 'failed' and progress > 0:
            raise serializers.ValidationError({
                'progress': _("Progress should be 0 when status is 'failed'")
            })

        # Validate completed_reports vs total_reports
        total_reports = data.get('total_reports')
        completed_reports = data.get('completed_reports')

        if total_reports is not None and completed_reports is not None:
            if completed_reports > total_reports:
                raise serializers.ValidationError({
                    'completed_reports': _(
                        "Completed reports cannot exceed total reports"
                    )
                })

            # Calculate expected progress
            expected_progress = int((completed_reports / total_reports) * 100) if total_reports > 0 else 0

            if abs(progress - expected_progress) > 10:  # Allow 10% tolerance
                raise serializers.ValidationError({
                    'progress': _(
                        "Progress (%(actual)d%%) doesn't match "
                        "completed reports (%(completed)d/%(total)d)"
                    ) % {
                        'actual': progress,
                        'completed': completed_reports,
                        'total': total_reports
                    }
                })

        # Validate download_url based on status
        download_url = data.get('download_url')
        if download_url and status != 'completed':
            raise serializers.ValidationError({
                'download_url': _("Download URL can only be provided when status is 'completed'")
            })

        return data
