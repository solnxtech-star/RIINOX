from rest_framework import serializers
from django.db import transaction
from django.utils.translation import gettext_lazy as _

from core.applications.academics.models import ClassRoom
from core.applications.accessments.models import AcademicSession, AcademicTerm
from core.applications.timetable.models import Subject


class AcademicSessionSerializer(serializers.ModelSerializer):
    school_name = serializers.CharField(source='school.name', read_only=True)
    term_count = serializers.SerializerMethodField()

    class Meta:
        model = AcademicSession
        fields = [
            "id", "school", "school_name", "name", "is_active",
            "term_count", "created_at", "updated_at"
        ]
        read_only_fields = ["id", "created_at", "updated_at", "school"]

    def get_term_count(self, obj):
        return obj.terms.count()

    def validate_name(self, value):
        import re
        pattern = r'^\d{4}[/-]\d{4}$'
        if not re.match(pattern, value):
            raise serializers.ValidationError(
                _("Academic session must be in format: YYYY/YYYY or YYYY-YYYY")
            )
        return value

    def create(self, validated_data):
        request = self.context.get("request")
        if not request or not hasattr(request.user, "school"):
            raise serializers.ValidationError("School context missing.")
        validated_data["school"] = request.user.school
        return super().create(validated_data)


class AcademicTermSerializer(serializers.ModelSerializer):
    session_name = serializers.CharField(source='session.name', read_only=True)
    is_current = serializers.SerializerMethodField()

    class Meta:
        model = AcademicTerm
        fields = [
            "id", "session", "session_name", "name",
            "term_type", "is_active", "is_current", "created_at", "updated_at"
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def get_is_current(self, obj):
        return obj.is_active and obj.session.is_active

    def validate(self, data):
        request = self.context.get("request")
        session = data.get("session") or getattr(self.instance, "session", None)

        if not request or not hasattr(request.user, "school"):
            raise serializers.ValidationError("School context missing.")

        if session and session.school != request.user.school:
            raise serializers.ValidationError(
                _("You cannot create a term in another school's session.")
            )

        name = data.get('name')
        if session and name:
            qs = AcademicTerm.objects.filter(session=session, name=name)
            if self.instance:
                qs = qs.exclude(id=self.instance.id)
            if qs.exists():
                raise serializers.ValidationError(
                    _(f"A term named '{name}' already exists in this session.")
                )

        return data


class AcademicStructureSetupSerializer(serializers.Serializer):
    session_name = serializers.CharField(max_length=20)
    terms = serializers.ListField(child=serializers.DictField(), min_length=1, max_length=4)
    automatic_activation = serializers.BooleanField(default=True)

    @transaction.atomic
    def create(self, validated_data):
        request = self.context.get("request")
        if not request or not hasattr(request.user, "school"):
            raise serializers.ValidationError("School context missing.")

        school = request.user.school
        session = AcademicSession.objects.create(
            school=school,
            name=validated_data["session_name"],
            is_active=validated_data["automatic_activation"]
        )

        terms = []
        for idx, term in enumerate(validated_data["terms"], start=1):
            terms.append(
                AcademicTerm.objects.create(
                    session=session,
                    name=term["name"],
                    term_type=term.get("term_type", "FULL_TERM"),
                    is_active=validated_data["automatic_activation"] and idx == 1
                )
            )

        return {"session": session, "terms": terms}


class SubjectSerializer(serializers.ModelSerializer):
    school_name = serializers.CharField(source='school.name', read_only=True)
    class_rooms = serializers.PrimaryKeyRelatedField(
        many=True,
        queryset=ClassRoom.objects.all(),
        required=False
    )

    class Meta:
        model = Subject
        fields = [
            "id", "school", "school_name", "name", "code",
            "description", "class_rooms",  # <-- added here
            "is_active", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at", "school"]

    def _get_school(self):
        request = self.context.get("request")
        if not request or not hasattr(request.user, "school"):
            raise serializers.ValidationError("School context missing.")
        return request.user.school

    def validate_class_rooms(self, value):
        """Ensure the classrooms belong to this user's school."""
        school = self._get_school()
        for classroom in value:
            if classroom.school != school:
                raise serializers.ValidationError(
                    f"ClassRoom '{classroom.name}' does not belong to your school."
                )
        return value

    def create(self, validated_data):
        class_rooms = validated_data.pop("class_rooms", [])
        validated_data["school"] = self._get_school()

        subject = super().create(validated_data)
        subject.class_rooms.set(class_rooms)
        return subject

    def update(self, instance, validated_data):
        class_rooms = validated_data.pop("class_rooms", None)
        subject = super().update(instance, validated_data)

        if class_rooms is not None:  # allow removing all linked classes
            subject.class_rooms.set(class_rooms)

        return subject
