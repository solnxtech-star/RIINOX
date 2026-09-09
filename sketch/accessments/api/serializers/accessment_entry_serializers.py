from rest_framework import serializers
from django.db import transaction
from django.utils.translation import gettext_lazy as _

from core.applications.academics.models import ClassRoom
from core.applications.accessments.models import AssessmentRecord, AssessmentType
from core.applications.timetable.models import Subject
from core.applications.users.models import StudentProfile


# ================================================================
# 1. AssessmentRecordSerializer
# ================================================================
class AssessmentRecordSerializer(serializers.ModelSerializer):
    student_name = serializers.CharField(source="student.user.name", read_only=True)
    student_id = serializers.CharField(source="student.student_id", read_only=True)

    # From TeachingAssignment → Subject.name
    subject_name = serializers.CharField(
        source="classroom_subject.subject.name", read_only=True
    )

    assessment_type_name = serializers.CharField(
        source="assessment_type.name", read_only=True
    )

    percentage_score = serializers.FloatField(read_only=True)

    class Meta:
        model = AssessmentRecord
        fields = [
            "id",
            "student",
            "student_name",
            "student_id",
            "classroom_subject",   # MUST be TeachingAssignment ID
            "subject_name",
            "assessment_type",
            "assessment_type_name",
            "index",
            "score",
            "percentage_score",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "percentage_score", "created_at", "updated_at"]

    # ----------------------------------------------------
    # VALIDATION
    # ----------------------------------------------------
    def validate(self, data):
        student = data.get("student")
        classroom_subject = data.get("classroom_subject")  # TeachingAssignment
        assessment_type = data.get("assessment_type")
        index = data.get("index")
        score = data.get("score")
        request = self.context.get("request")

        # -----------------------------------
        # 0. Ensure classroom_subject is TeachingAssignment
        # -----------------------------------
        from core.applications.academics.models import TeachingAssignment

        if classroom_subject and not isinstance(classroom_subject, TeachingAssignment):
            raise serializers.ValidationError({
                "classroom_subject": "Invalid ID. You must pass a TeachingAssignment ID."
            })

        # -----------------------------------
        # 1. MULTI-TENANT VALIDATION — all must belong to same school
        # -----------------------------------
        school = request.user.school

        if student and student.school != school:
            raise serializers.ValidationError({"student": "Student does not belong to your school."})

        if classroom_subject and classroom_subject.classroom.school != school:
            raise serializers.ValidationError({"classroom_subject": "This class does not belong to your school."})

        if classroom_subject and classroom_subject.subject.school != school:
            raise serializers.ValidationError({"classroom_subject": "This subject does not belong to your school."})

        # -----------------------------------
        # 2. VALIDATE STUDENT CLASSROOM MEMBERSHIP
        # -----------------------------------
        if student and classroom_subject:
            if student.classroom_id != classroom_subject.classroom_id:
                raise serializers.ValidationError({
                    "student": _(
                        "Student %(student)s is not enrolled in class %(classroom)s"
                    ) % {
                        "student": student.user.name,
                        "classroom": classroom_subject.classroom.__str__(),
                    }
                })

        # -----------------------------------
        # 3. VALIDATE INDEX LIMIT
        # -----------------------------------
        if assessment_type and index:
            if index > assessment_type.count:
                raise serializers.ValidationError({
                    "index": _(
                        "Index %(index)d exceeds allowed maximum of %(count)d"
                    ) % {"index": index, "count": assessment_type.count}
                })

        # -----------------------------------
        # 4. VALIDATE SCORE LIMIT
        # -----------------------------------
        if score is not None and assessment_type:
            if score > assessment_type.max_score:
                raise serializers.ValidationError({
                    "score": _(
                        "Score %(score).2f exceeds maximum allowed score of %(max).2f"
                    ) % {"score": score, "max": assessment_type.max_score}
                })

        return data

    # ----------------------------------------------------
    # CREATE: AUTO-CALCULATE PERCENTAGE SCORE
    # ----------------------------------------------------
    def create(self, validated_data):
        score = validated_data.get("score")
        assessment_type = validated_data.get("assessment_type")

        if score is not None and assessment_type.max_score > 0:
            validated_data["percentage_score"] = (
                (score / assessment_type.max_score) * 100
            )

        return super().create(validated_data)

# ================================================================
# 2. AssessmentEntryFormDataSerializer
# ================================================================
class AssessmentEntryFormDataSerializer(serializers.Serializer):
    class_room_id = serializers.IntegerField()
    subject_id = serializers.IntegerField()

    def validate(self, data):
        request = self.context["request"]
        school = request.user.school

        # Classroom
        try:
            class_room = ClassRoom.objects.get(id=data["class_room_id"], school=school)
        except ClassRoom.DoesNotExist:
            raise serializers.ValidationError(
                {"class_room_id": _("Classroom not found in your school")}
            )

        # Subject
        try:
            subject = Subject.objects.get(
                id=data["subject_id"], school=school, is_active=True
            )
        except Subject.DoesNotExist:
            raise serializers.ValidationError(
                {"subject_id": _("Subject not found or inactive in your school")}
            )

        data["class_room"] = class_room
        data["subject"] = subject
        return data


# ================================================================
# 3. StudentScoreEntrySerializer
# ================================================================
class StudentScoreEntrySerializer(serializers.Serializer):
    student_id = serializers.IntegerField()
    score = serializers.FloatField(min_value=0)

    def validate(self, data):
        if data["score"] < 0:
            raise serializers.ValidationError({"score": "Score cannot be negative"})
        return data


# ================================================================
# 4. BulkAssessmentEntrySerializer
# ================================================================
class BulkAssessmentEntrySerializer(serializers.Serializer):
    classroom_subject_id = serializers.IntegerField()
    assessment_type_id = serializers.IntegerField()
    index = serializers.IntegerField(min_value=1)
    entries = serializers.ListField(
        child=StudentScoreEntrySerializer(),
        min_length=1,
    )

    def validate(self, data):
        request = self.context["request"]
        school = request.user.school

        from core.applications.timetable.models import ClassroomSubject

        # Validate classroom_subject
        try:
            classroom_subject = ClassroomSubject.objects.get(
                id=data["classroom_subject_id"],
                class_room__school=school,
            )
        except ClassroomSubject.DoesNotExist:
            raise serializers.ValidationError(
                {"classroom_subject_id": _("Classroom-subject not found")}
            )

        # Validate assessment_type
        try:
            assessment_type = AssessmentType.objects.get(
                id=data["assessment_type_id"],
                policy__school=school,
            )
        except AssessmentType.DoesNotExist:
            raise serializers.ValidationError(
                {"assessment_type_id": _("Assessment type not found")}
            )

        # Validate index
        if data["index"] > assessment_type.count:
            raise serializers.ValidationError(
                {"index": _("Index exceeds allowed count")}
            )

        # Validate students belong to classroom
        student_ids = [entry["student_id"] for entry in data["entries"]]
        valid = StudentProfile.objects.filter(
            id__in=student_ids,
            class_room=classroom_subject.class_room,
            school=school,
        ).values_list("id", flat=True)

        invalid = set(student_ids) - set(valid)
        if invalid:
            raise serializers.ValidationError(
                {
                    "entries": _(
                        "Invalid students for classroom %(classroom)s: %(students)s"
                    )
                    % {
                        "classroom": classroom_subject.class_room,
                        "students": ", ".join(map(str, invalid)),
                    }
                }
            )

        data["classroom_subject"] = classroom_subject
        data["assessment_type"] = assessment_type
        return data

    @transaction.atomic
    def create(self, validated_data):
        classroom_subject = validated_data["classroom_subject"]
        assessment_type = validated_data["assessment_type"]
        index = validated_data["index"]

        created_records = []

        for entry in validated_data["entries"]:
            student_id = entry["student_id"]
            score = entry["score"]

            record, created = AssessmentRecord.objects.update_or_create(
                student_id=student_id,
                classroom_subject=classroom_subject,
                assessment_type=assessment_type,
                index=index,
                defaults={"score": score},
            )

            # Calculate percentage
            record.percentage_score = (score / assessment_type.max_score) * 100
            record.save()

            created_records.append(record)

        return {
            "message": f"Successfully created {len(created_records)} records",
            "records": AssessmentRecordSerializer(created_records, many=True).data,
            "count": len(created_records),
        }
