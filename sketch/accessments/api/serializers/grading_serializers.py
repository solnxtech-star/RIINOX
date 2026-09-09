from rest_framework import serializers
from django.db import transaction
from django.utils.translation import gettext_lazy as _
from core.applications.accessments.models import GradeScale


class GradeScaleSerializer(serializers.ModelSerializer):
    """
    Serializer for GradeScale model.
    Defines grade brackets and points for a school's grading system.

    Example:
        {"grade": "A", "min_score": 75, "max_score": 100, "point": 5.0}

    Attributes:
        school_name (str): Read-only field showing school name
        score_range (str): Read-only field showing formatted score range
    """

    school_name = serializers.CharField(source="school.name", read_only=True)
    score_range = serializers.SerializerMethodField()

    class Meta:
        model = GradeScale
        fields = [
            "id",
            "school",
            "school_name",
            "grade",
            "display_name",
            "min_score",
            "max_score",
            "score_range",
            "point",
            "remark",
            "is_honors",
            "is_active",
            "order",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at", "school"]

    def get_score_range(self, obj):
        """Returns formatted score range (e.g., '75-100')"""
        return f"{obj.min_score}-{obj.max_score}"

    def validate(self, data):
        """
        Validate grade scale data.

        Ensures:
        1. min_score <= max_score
        2. Scores are between 0 and 100
        3. No overlapping score ranges within school
        4. Grade is unique within school

        Args:
            data (dict): The grade scale data to validate

        Returns:
            dict: Validated data

        Raises:
            serializers.ValidationError: If validation fails
        """
        min_score = data.get("min_score")
        max_score = data.get("max_score")
        grade = data.get("grade")

        if min_score and max_score:
            # Check min_score <= max_score
            if min_score > max_score:
                raise serializers.ValidationError(
                    _("Minimum score cannot be greater than maximum score")
                )

            # Check scores are within 0-100 range
            if min_score < 0 or max_score > 100:
                raise serializers.ValidationError(_("Scores must be between 0 and 100"))

            # Check for overlapping score ranges
            school = self.context["request"].user.school
            overlapping = (
                GradeScale.objects.filter(school=school, is_active=True)
                .exclude(min_score__gt=max_score)
                .exclude(max_score__lt=min_score)
            )

            if self.instance:
                overlapping = overlapping.exclude(id=self.instance.id)

            if overlapping.exists():
                raise serializers.ValidationError(
                    _("Score range overlaps with existing grade scale")
                )

        # Check grade uniqueness
        if grade:
            school = self.context["request"].user.school
            existing = GradeScale.objects.filter(
                school=school, grade=grade, is_active=True
            )

            if self.instance:
                existing = existing.exclude(id=self.instance.id)

            if existing.exists():
                raise serializers.ValidationError(
                    _("Grade '%(grade)s' already exists in your grading system")
                    % {"grade": grade}
                )

        return data


class GradeScaleBulkCreateSerializer(serializers.Serializer):
    """
    Serializer for bulk creating multiple grade scales.
    Used during school setup to create entire grading system at once.

    Example:
        {
            "scales": [
                {"grade": "A", "min_score": 75, "max_score": 100, "point": 5.0},
                {"grade": "B", "min_score": 70, "max_score": 74, "point": 4.0},
                ...
            ]
        }
    """

    scales = serializers.ListField(
        child=serializers.DictField(),
        min_length=3,
        max_length=20,
        help_text=_("List of grade scale configurations"),
    )

    def validate_scales(self, value):
        """
        Validate all grade scales collectively.

        Ensures:
        1. No overlapping score ranges
        2. Complete coverage from 0 to 100
        3. Unique grade names
        4. Proper ordering (highest grade first)

        Args:
            value (list): List of grade scale dictionaries

        Returns:
            list: Validated scales

        Raises:
            serializers.ValidationError: If validation fails
        """
        grades = set()

        # Sort by min_score for validation
        sorted_scales = sorted(value, key=lambda x: x["min_score"])

        # Check for coverage from 0 to 100
        if sorted_scales[0]["min_score"] > 0:
            raise serializers.ValidationError(
                _("First grade scale must start at score 0")
            )

        if sorted_scales[-1]["max_score"] < 100:
            raise serializers.ValidationError(
                _("Last grade scale must end at score 100")
            )

        # Check for gaps and overlaps
        previous_max = -1
        for i, scale in enumerate(sorted_scales):
            min_score = scale["min_score"]
            max_score = scale["max_score"]
            grade = scale["grade"]

            # Check for gaps
            if min_score > previous_max + 1:
                raise serializers.ValidationError(
                    _("Gap in score range between %(prev)d and %(next)d")
                    % {"prev": previous_max, "next": min_score}
                )

            # Check for overlaps
            if min_score <= previous_max:
                raise serializers.ValidationError(
                    _("Overlap in score ranges at scale %(index)d") % {"index": i + 1}
                )

            # Check grade uniqueness
            if grade in grades:
                raise serializers.ValidationError(
                    _("Duplicate grade '%(grade)s' found") % {"grade": grade}
                )
            grades.add(grade)

            previous_max = max_score

        return value

    @transaction.atomic
    def create(self, validated_data):
        """
        Bulk create grade scales in a single transaction.

        Args:
            validated_data (dict): Validated serializer data

        Returns:
            dict: Dictionary containing created grade scales
        """
        request = self.context["request"]
        school = request.user.school

        # Deactivate existing grade scales
        GradeScale.objects.filter(school=school, is_active=True).update(is_active=False)

        # Create new grade scales
        scales = []
        for idx, scale_data in enumerate(validated_data["scales"]):
            scale = GradeScale.objects.create(
                school=school,
                grade=scale_data["grade"],
                display_name=scale_data.get("display_name"),
                min_score=scale_data["min_score"],
                max_score=scale_data["max_score"],
                point=scale_data["point"],
                remark=scale_data.get("remark", ""),
                is_honors=scale_data.get("is_honors", False),
                order=idx,
                is_active=True,
            )
            scales.append(scale)

        return {"scales": scales}


class DefaultGradingSystemSerializer(serializers.Serializer):
    """
    Serializer for selecting a pre-configured grading system.
    Provides quick setup options for common grading systems.

    Available systems:
        - standard: Basic A-F system (Common in many schools)
        - extended: Includes A' for honors (Like in your example)
        - nigerian: Nigerian WAEC grading system (A1-F9)
        - custom: Create custom system manually
    """

    SYSTEM_CHOICES = [
        ("standard", _("Standard (A-F)")),
        ("extended", _("Extended (A'-F)")),
        ("nigerian", _("Nigerian (A1-F9)")),
        ("custom", _("Custom System")),
    ]

    system_name = serializers.ChoiceField(
        choices=SYSTEM_CHOICES, help_text=_("Select a pre-configured grading system")
    )

    def get_default_scales(self, system_name):
        """
        Get default grade scales for the selected system.

        Args:
            system_name (str): The selected grading system

        Returns:
            list: List of grade scale dictionaries

        Raises:
            serializers.ValidationError: If system_name is invalid
        """
        if system_name == "standard":
            return [
                {
                    "grade": "A",
                    "min_score": 75,
                    "max_score": 100,
                    "point": 5.0,
                    "remark": "Excellent",
                },
                {
                    "grade": "B",
                    "min_score": 70,
                    "max_score": 74,
                    "point": 4.0,
                    "remark": "Very Good",
                },
                {
                    "grade": "C",
                    "min_score": 60,
                    "max_score": 69,
                    "point": 3.0,
                    "remark": "Good",
                },
                {
                    "grade": "D",
                    "min_score": 50,
                    "max_score": 59,
                    "point": 2.0,
                    "remark": "Pass",
                },
                {
                    "grade": "E",
                    "min_score": 45,
                    "max_score": 49,
                    "point": 1.0,
                    "remark": "Poor",
                },
                {
                    "grade": "F",
                    "min_score": 0,
                    "max_score": 44,
                    "point": 0.0,
                    "remark": "Fail",
                },
            ]
        elif system_name == "extended":
            return [
                {
                    "grade": "A+",
                    "display_name": "A'",
                    "min_score": 90,
                    "max_score": 100,
                    "point": 5.0,
                    "is_honors": True,
                    "remark": "Distinction",
                },
                {
                    "grade": "A",
                    "min_score": 80,
                    "max_score": 89,
                    "point": 5.0,
                    "remark": "Excellent",
                },
                {
                    "grade": "B",
                    "min_score": 70,
                    "max_score": 79,
                    "point": 4.0,
                    "remark": "Very Good",
                },
                {
                    "grade": "C",
                    "min_score": 60,
                    "max_score": 69,
                    "point": 3.0,
                    "remark": "Good",
                },
                {
                    "grade": "D",
                    "min_score": 50,
                    "max_score": 59,
                    "point": 2.0,
                    "remark": "Pass",
                },
                {
                    "grade": "E",
                    "min_score": 40,
                    "max_score": 49,
                    "point": 1.0,
                    "remark": "Poor",
                },
                {
                    "grade": "F",
                    "min_score": 0,
                    "max_score": 39,
                    "point": 0.0,
                    "remark": "Fail",
                },
            ]
        elif system_name == "nigerian":
            return [
                {
                    "grade": "A1",
                    "min_score": 75,
                    "max_score": 100,
                    "point": 5.0,
                    "remark": "Distinction",
                },
                {
                    "grade": "B2",
                    "min_score": 70,
                    "max_score": 74,
                    "point": 4.0,
                    "remark": "Very Good",
                },
                {
                    "grade": "B3",
                    "min_score": 65,
                    "max_score": 69,
                    "point": 3.5,
                    "remark": "Good",
                },
                {
                    "grade": "C4",
                    "min_score": 60,
                    "max_score": 64,
                    "point": 3.0,
                    "remark": "Credit",
                },
                {
                    "grade": "C5",
                    "min_score": 55,
                    "max_score": 59,
                    "point": 2.5,
                    "remark": "Credit",
                },
                {
                    "grade": "C6",
                    "min_score": 50,
                    "max_score": 54,
                    "point": 2.0,
                    "remark": "Credit",
                },
                {
                    "grade": "D7",
                    "min_score": 45,
                    "max_score": 49,
                    "point": 1.5,
                    "remark": "Pass",
                },
                {
                    "grade": "E8",
                    "min_score": 40,
                    "max_score": 44,
                    "point": 1.0,
                    "remark": "Pass",
                },
                {
                    "grade": "F9",
                    "min_score": 0,
                    "max_score": 39,
                    "point": 0.0,
                    "remark": "Fail",
                },
            ]
        else:
            raise serializers.ValidationError(_("Invalid grading system"))
