from rest_framework import serializers
from django.db import transaction, models
from django.utils.translation import gettext_lazy as _

from core.applications.accessments.models import AcademicTerm, AssessmentPolicy, AssessmentType


class AssessmentTypeSerializer(serializers.ModelSerializer):
    """
    Serializer for AssessmentType model.
    Defines categories of assessments that contribute to total grade.

    Examples:
        - Test: 2 occurrences, 40% weight
        - Exam: 1 occurrence, 60% weight
        - Assignment: 1 occurrence, 10% weight

    Attributes:
        policy_name (str): Read-only field showing parent policy name
        category_display (str): Read-only field showing human-readable category
    """

    policy_name = serializers.CharField(source="policy.name", read_only=True)
    category_display = serializers.CharField(source="get_category_display", read_only=True)

    class Meta:
        model = AssessmentType
        fields = [
            "id", "policy", "policy_name", "name", "category",
            "category_display", "count", "weight", "max_score",
            "is_optional", "order", "created_at", "updated_at"
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate(self, data):
        """
        Validate assessment type data.

        Ensures:
        1. Weight doesn't cause total to exceed 100%
        2. Count is at least 1
        3. Max score is positive

        Args:
            data (dict): The assessment type data to validate

        Returns:
            dict: Validated data

        Raises:
            serializers.ValidationError: If validation fails
        """
        policy = data.get('policy') or (self.instance.policy if self.instance else None)
        weight = data.get('weight')

        if policy and weight is not None:
            # Calculate total weight of all assessment types in this policy
            total_weight = policy.assessment_types.exclude(
                id=self.instance.id if self.instance else None
            ).aggregate(
                total=models.Sum('weight')
            )['total'] or 0

            # Check if adding this weight would exceed 100%
            if total_weight + weight > 100:
                raise serializers.ValidationError({
                    'weight': _(
                        "Total weight would exceed 100%. "
                        "Current total: %(current)d%, "
                        "Adding: %(adding)d%"
                    ) % {'current': total_weight, 'adding': weight}
                })

        # Validate count
        if 'count' in data and data['count'] < 1:
            raise serializers.ValidationError({
                'count': _("Count must be at least 1")
            })

        # Validate max_score
        if 'max_score' in data and data['max_score'] <= 0:
            raise serializers.ValidationError({
                'max_score': _("Maximum score must be positive")
            })

        return data


class AssessmentPolicySerializer(serializers.ModelSerializer):
    """
    Serializer for AssessmentPolicy model.
    Defines grading configuration for continuous assessments per term.

    Example:
        School: Earlygrip High School
        Term: 1st Term 2024/2025
        Configuration: Tests (40%) + Exam (60%)

    Attributes:
        school_name (str): Read-only field showing school name
        term_name (str): Read-only field showing term name
        assessment_types (list): Nested serializer for assessment types
        total_weight (int): Read-only field showing total weight percentage
    """

    school_name = serializers.CharField(source='school.name', read_only=True)
    term_name = serializers.CharField(source='term.name', read_only=True)
    assessment_types = AssessmentTypeSerializer(many=True, read_only=True)
    total_weight = serializers.SerializerMethodField()

    class Meta:
        model = AssessmentPolicy
        fields = [
            'id', 'school', 'school_name', 'term', 'term_name', 'name',
            'is_active', 'ca_weight', 'exam_weight', 'assessment_types',
            'total_weight', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'school']

    def get_total_weight(self, obj):
        """
        Calculate total weight of all assessment types in this policy.

        Args:
            obj (AssessmentPolicy): The assessment policy instance

        Returns:
            int: Total weight percentage
        """
        return obj.assessment_types.aggregate(
            total=models.Sum('weight')
        )['total'] or 0

    def validate(self, data):
        """
        Validate assessment policy data.

        Ensures:
        1. CA weight + Exam weight = 100%
        2. Only one active policy per school/term

        Args:
            data (dict): The assessment policy data to validate

        Returns:
            dict: Validated data

        Raises:
            serializers.ValidationError: If validation fails
        """
        # Check CA + Exam weights sum to 100%
        ca_weight = data.get('ca_weight', getattr(self.instance, 'ca_weight', 0))
        exam_weight = data.get('exam_weight', getattr(self.instance, 'exam_weight', 0))

        if ca_weight + exam_weight != 100:
            raise serializers.ValidationError({
                'ca_weight': _("CA weight and Exam weight must sum to 100%"),
                'exam_weight': _("CA weight and Exam weight must sum to 100%")
            })

        # Check for unique active policy per school/term
        school = data.get('school') or (self.instance.school if self.instance else None)
        term = data.get('term') or (self.instance.term if self.instance else None)
        is_active = data.get('is_active', True)

        if school and term and is_active:
            # Look for other active policies for same school/term
            query = AssessmentPolicy.objects.filter(
                school=school,
                term=term,
                is_active=True
            )

            if self.instance:
                query = query.exclude(id=self.instance.id)

            if query.exists():
                existing_policy = query.first()
                raise serializers.ValidationError({
                    'is_active': _(
                        "An active policy already exists for %(term)s. "
                        "Please deactivate '%(policy)s' first."
                    ) % {'term': term, 'policy': existing_policy.name}
                })

        return data


class DefaultAssessmentPolicySerializer(serializers.Serializer):
    """
    Serializer for creating default assessment policies.
    Provides pre-configured assessment setups for quick school configuration.

    Available configurations:
        - standard_60_40: Exam 60% + Tests 40% (Common in many schools)
        - half_term: CA 50% + Half Term Exam 50%
        - detailed: Multiple assessment types (Tests, Assignments, Projects, Exam)
    """

    CONFIG_CHOICES = [
        ('standard_60_40', _('Standard: Exam 60% + Tests 40%')),
        ('half_term', _('Half Term: CA 50% + Half Term Exam 50%')),
        ('detailed', _('Detailed: Multiple assessment types')),
    ]

    term = serializers.PrimaryKeyRelatedField(
        queryset=AcademicTerm.objects.all(),
        help_text=_("Academic term for this assessment policy")
    )
    config_type = serializers.ChoiceField(
        choices=CONFIG_CHOICES,
        default='standard_60_40',
        help_text=_("Select a pre-configured assessment setup")
    )
    policy_name = serializers.CharField(
        max_length=150,
        default="Default Assessment Policy",
        help_text=_("Name for this assessment policy")
    )

    def validate(self, data):
        """
        Validate that term belongs to the user's school.

        Args:
            data (dict): The serializer data to validate

        Returns:
            dict: Validated data

        Raises:
            serializers.ValidationError: If term doesn't belong to user's school
        """
        request = self.context['request']
        school = request.user.school
        term = data['term']

        if term.session.school != school:
            raise serializers.ValidationError({
                'term': _("Selected term does not belong to your school")
            })

        return data

    @transaction.atomic
    def create(self, validated_data):
        """
        Create assessment policy with default configuration.

        Args:
            validated_data (dict): Validated serializer data

        Returns:
            AssessmentPolicy: Created assessment policy with assessment types
        """
        request = self.context['request']
        school = request.user.school
        term = validated_data['term']
        config_type = validated_data['config_type']
        policy_name = validated_data['policy_name']

        # Deactivate any existing active policy for this term
        AssessmentPolicy.objects.filter(
            school=school,
            term=term,
            is_active=True
        ).update(is_active=False)

        # Create assessment policy based on configuration type
        if config_type == 'standard_60_40':
            policy = AssessmentPolicy.objects.create(
                school=school,
                term=term,
                name=policy_name,
                ca_weight=40,
                exam_weight=60,
                is_active=True
            )

            # Create standard assessment types
            AssessmentType.objects.create(
                policy=policy,
                name="Test",
                category="CA",
                count=2,
                weight=40,
                max_score=30,
                order=1
            )

            AssessmentType.objects.create(
                policy=policy,
                name="Exam",
                category="EXAM",
                count=1,
                weight=60,
                max_score=60,
                order=2
            )

        elif config_type == 'half_term':
            policy = AssessmentPolicy.objects.create(
                school=school,
                term=term,
                name=policy_name,
                ca_weight=50,
                exam_weight=50,
                is_active=True
            )

            # Create half-term assessment types
            AssessmentType.objects.create(
                policy=policy,
                name="Continuous Assessment",
                category="CA",
                count=1,
                weight=50,
                max_score=100,
                order=1
            )

            AssessmentType.objects.create(
                policy=policy,
                name="Half Term Exam",
                category="HALF_TERM",
                count=1,
                weight=50,
                max_score=100,
                order=2
            )

        elif config_type == 'detailed':
            policy = AssessmentPolicy.objects.create(
                school=school,
                term=term,
                name=policy_name,
                ca_weight=40,
                exam_weight=60,
                is_active=True
            )

            # Create detailed assessment types
            AssessmentType.objects.create(
                policy=policy,
                name="Test",
                category="CA",
                count=2,
                weight=30,
                max_score=20,
                order=1
            )

            AssessmentType.objects.create(
                policy=policy,
                name="Assignment",
                category="CA",
                count=2,
                weight=10,
                max_score=10,
                order=2
            )

            AssessmentType.objects.create(
                policy=policy,
                name="Exam",
                category="EXAM",
                count=1,
                weight=60,
                max_score=60,
                order=3
            )

        return policy
