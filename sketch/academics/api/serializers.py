from rest_framework import serializers



from django.db import IntegrityError

from core.applications.timetable.models import Subject
from core.applications.users.models import TeacherProfile
from core.applications.academics.models import ClassRoom, TeachingAssignment




class TeacherListSerializer(serializers.ModelSerializer):
    """
    Serializer for listing all teacher profiles.
    Used in endpoint: GET /teachers/
    """
    email = serializers.EmailField(source="user.email")

    class Meta:
        model = TeacherProfile
        fields = [
            "id",
            "email",
            "qualification",
            "specialization",
            "department",
            "staff_id",
        ]


# ============================================================
#  TEACHER DETAIL SERIALIZER
# ============================================================
class TeacherDetailSerializer(serializers.ModelSerializer):
    """
    Serializer for retrieving a single teacher's full profile.
    Includes assigned classrooms.
    """
    email = serializers.EmailField(source="user.email")
    classrooms = serializers.SerializerMethodField()

    class Meta:
        model = TeacherProfile
        fields = [
            "id",
            "email",
            "qualification",
            "specialization",
            "department",
            "staff_id",
            "classrooms",
        ]

    def get_classrooms(self, obj):
        """Returns all classrooms assigned to the teacher."""
        return [
            {
                "id": c.id,
                "name": c.arm,
                "level": c.school,
                "arm": c.academic_class,
            }
            for c in obj.classrooms.all()
        ]


# ============================================================
#  ADMIN → ASSIGN CLASSROOMS TO TEACHER
# ============================================================
class AdminAssignClassroomsSerializer(serializers.Serializer):
    """
    ADMIN ACTION:
    Assign classrooms to a teacher profile.

    - Ensures all supplied classroom IDs belong to the admin's school.
    - Completely replaces existing classroom assignments.
    """
    classroom_ids = serializers.ListField(
        child=serializers.CharField(),
        allow_empty=False,
        help_text="List of classroom UUIDs to assign."
    )

    def validate_classroom_ids(self, ids):
        """Validate IDs exist in admin's school."""
        request = self.context["request"]
        school = request.user.school

        classrooms = ClassRoom.objects.filter(id__in=ids, school=school)

        if classrooms.count() != len(ids):
            raise serializers.ValidationError(
                "Some classrooms do not exist or do not belong to your school."
            )
        return ids

    def save(self, teacher_profile):
        """Assign classrooms to teacher."""
        ids = self.validated_data["classroom_ids"]
        teacher_profile.classrooms.set(ids)
        teacher_profile.save()
        return teacher_profile


# ============================================================
#  TEACHER → CREATE NEW ASSIGNMENTS
# ============================================================
class TeacherCreateTeachingAssignmentsSerializer(serializers.Serializer):
    """
    TEACHER ACTION:
    Create multiple teaching assignments.

    Expected input:
        assignments = [
            {"classroom": UUID, "subject": UUID},
            {"classroom": UUID, "subject": UUID},
        ]

    Features:
        - Validates school ownership
        - Prevents creating duplicates
        - Returns list of created or existing assignments
    """
    assignments = serializers.ListField(
        child=serializers.DictField(),
        allow_empty=False
    )

    def validate_assignments(self, items):
        teacher = self.context["teacher"]
        school = teacher.user.school

        for item in items:
            classroom_id = item.get("classroom")
            subject_id = item.get("subject")

            if not classroom_id or not subject_id:
                raise serializers.ValidationError("classroom and subject are required.")

            # Validate classroom
            if not ClassRoom.objects.filter(id=classroom_id, school=school).exists():
                raise serializers.ValidationError(
                    f"Invalid classroom {classroom_id} for this teacher."
                )

            # Validate subject
            if not Subject.objects.filter(id=subject_id, school=school).exists():
                raise serializers.ValidationError(
                    f"Invalid subject {subject_id} for this teacher."
                )

        return items

    def save(self):
        teacher = self.context["teacher"]
        created_assignments = []

        for item in self.validated_data["assignments"]:
            classroom_id = item["classroom"]
            subject_id = item["subject"]

            # Avoid duplicates
            assignment, created = TeachingAssignment.objects.get_or_create(
                teacher=teacher,
                classroom_id=classroom_id,
                subject_id=subject_id,
            )
            created_assignments.append(assignment)

        return created_assignments


# ============================================================
#  TEACHER → REASSIGN / UPDATE AN EXISTING ASSIGNMENT
# ============================================================
class TeacherReassignTeachingAssignmentSerializer(serializers.Serializer):
    """
    TEACHER ACTION:
    Update a single teaching assignment by changing classroom OR subject.

    Fields:
        classroom: new classroom UUID (optional)
        subject: new subject UUID (optional)

    Validations:
        - New classroom & subject must belong to same school
        - Prevent duplicates (cannot reassign to an existing combination)
    """
    classroom = serializers.UUIDField(required=False)
    subject = serializers.UUIDField(required=False)

    def validate(self, attrs):
        teacher = self.context["teacher"]
        assignment = self.context["assignment"]
        school = teacher.user.school

        new_classroom = attrs.get("classroom", assignment.classroom_id)
        new_subject = attrs.get("subject", assignment.subject_id)

        # Validate classroom
        if not ClassRoom.objects.filter(id=new_classroom, school=school).exists():
            raise serializers.ValidationError("Invalid classroom selected.")

        # Validate subject
        if not Subject.objects.filter(id=new_subject, school=school).exists():
            raise serializers.ValidationError("Invalid subject selected.")

        # Check for duplicate combination
        if TeachingAssignment.objects.filter(
            teacher=teacher,
            classroom_id=new_classroom,
            subject_id=new_subject
        ).exclude(id=assignment.id).exists():
            raise serializers.ValidationError(
                "This assignment already exists. Cannot create duplicate."
            )

        attrs["new_classroom"] = new_classroom
        attrs["new_subject"] = new_subject
        return attrs

    def save(self):
        assignment = self.context["assignment"]

        assignment.classroom_id = self.validated_data["new_classroom"]
        assignment.subject_id = self.validated_data["new_subject"]
        assignment.save()

        return assignment
