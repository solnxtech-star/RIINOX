from rest_framework import serializers

from core.applications.timetable.models import ClassSchedule
from core.applications.timetable.models import Subject
from core.applications.timetable.models import TimeSlot
from core.applications.timetable.models import Timetable
from core.applications.users.api.serializers.serializers import GetUser
from core.applications.users.models import User
from core.helper.enums import UserRole


class SubjectSerializer(serializers.ModelSerializer):
    school_id = serializers.IntegerField(read_only=True)

    class Meta:
        model = Subject
        fields = [
            "id",
            "school_id",
            "name",
            "code",
            "description",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "is_active", "created_at", "updated_at"]

    def create(self, validated_data):
        # Inject the acting admin’s school
        validated_data["school"] = self.context["request"].user.adminprofile.school
        return super().create(validated_data)

    def validate_name(self, value):
        if len(value) < 2:
            raise serializers.ValidationError("Subject name is too short.")
        return value

    def validate_code(self, value):
        if " " in value:
            raise serializers.ValidationError("Subject code cannot contain spaces.")
        return value.upper()


class TimeSlotSerializer(serializers.ModelSerializer):
    """Serializer for TimeSlot model"""

    class Meta:
        model = TimeSlot
        fields = [
            "id",
            "name",
            "start_time",
            "end_time",
            "is_break",
            "order",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class ClassScheduleSerializer(serializers.ModelSerializer):
    """Serializer for ClassSchedule model with nested relations"""

    subject = SubjectSerializer(read_only=True)
    time_slot = TimeSlotSerializer(read_only=True)
    teacher = GetUser(read_only=True)

    subject_id = serializers.PrimaryKeyRelatedField(
        queryset=Subject.objects.filter(is_active=True),
        source="subject",
        write_only=True,
        required=False,
        allow_null=True,
    )
    time_slot_id = serializers.PrimaryKeyRelatedField(
        queryset=TimeSlot.objects.all(),
        source="time_slot",
        write_only=True,
    )
    teacher_id = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.filter(
            memberships__role=UserRole.TEACHER
        ).distinct(),
        source="teacher",
        write_only=True,
        required=False,
        allow_null=True,
    )

    class Meta:
        model = ClassSchedule
        fields = [
            "id",
            "academic_class",
            "day_of_week",
            "time_slot",
            "time_slot_id",
            "subject",
            "subject_id",
            "teacher",
            "teacher_id",
            "room_number",
            "is_active",
            "notes",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class ClassScheduleListSerializer(serializers.ModelSerializer):
    """Simplified serializer for listing schedules"""

    subject_name = serializers.CharField(source="subject.name", read_only=True)
    subject_code = serializers.CharField(source="subject.code", read_only=True)
    teacher_name = serializers.SerializerMethodField()
    time_slot_name = serializers.CharField(source="time_slot.name", read_only=True)
    start_time = serializers.TimeField(source="time_slot.start_time", read_only=True)
    end_time = serializers.TimeField(source="time_slot.end_time", read_only=True)
    is_break = serializers.BooleanField(source="time_slot.is_break", read_only=True)

    class Meta:
        model = ClassSchedule
        fields = [
            "id",
            "academic_class",
            "day_of_week",
            "subject_name",
            "subject_code",
            "teacher_name",
            "time_slot_name",
            "start_time",
            "end_time",
            "is_break",
            "room_number",
            "notes",
        ]

    def get_teacher_name(self, obj):
        return obj.teacher.get_full_name() if obj.teacher else None


class StudentTimetableSerializer(serializers.ModelSerializer):
    """Serializer for student's weekly timetable view"""

    schedules = serializers.SerializerMethodField()

    class Meta:
        model = Timetable
        fields = [
            "id",
            "name",
            "academic_year",
            "term",
            "start_date",
            "end_date",
            "schedules",
        ]

    def get_schedules(self, obj):
        # Get student's class from context
        request = self.context.get("request")
        if not request or not hasattr(request.user, "studentprofile"):
            return []

        student_class = request.user.studentprofile.current_class

        # Get schedules for student's class
        schedules = (
            obj.schedules.filter(
                academic_class=student_class,
                is_active=True,
            )
            .select_related("subject", "teacher", "time_slot")
            .order_by(
                "day_of_week",
                "time_slot__order",
            )
        )

        return ClassScheduleListSerializer(schedules, many=True).data


class TimetableSerializer(serializers.ModelSerializer):
    """Full serializer for Timetable model"""

    schedules = ClassScheduleListSerializer(many=True, read_only=True)
    schedule_ids = serializers.PrimaryKeyRelatedField(
        queryset=ClassSchedule.objects.all(),
        source="schedules",
        write_only=True,
        many=True,
        required=False,
    )

    class Meta:
        model = Timetable
        fields = [
            "id",
            "name",
            "academic_year",
            "term",
            "start_date",
            "end_date",
            "is_active",
            "schedules",
            "schedule_ids",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]
