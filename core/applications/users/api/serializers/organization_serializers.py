import re
from typing import Any

import phonenumbers
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from core.applications.invoice import services as document_services
from core.applications.subscriptions.models import Plan
from core.applications.users import reference_data
from core.applications.users import services
from core.applications.users.errors import domain_errors
from core.applications.users.invitation_services import assignable_roles
from core.applications.users.invitation_services import mask_email
from core.applications.users.models import BusinessType
from core.applications.users.models import Invitation
from core.applications.users.models import Membership
from core.applications.users.models import Organization
from core.applications.users.models import OrganizationBusinessType
from core.applications.users.models import Role
from core.applications.users.models import State

REGISTRATION_NUMBER_RE = re.compile(r"[A-Z0-9\-/]{3,30}")



# ===========================================================================
# 1. Plans & business types
# ===========================================================================
class PlanSerializer(serializers.ModelSerializer):
    """The subscription Plan linked to an organization."""

    class Meta:
        model = Plan
        fields = ["id", "name", "description", "price", "is_active"]


class BusinessTypeSerializer(serializers.ModelSerializer):
    """Catalog entry; feeds the onboarding cards."""

    class Meta:
        model = BusinessType
        fields = ["id", "code", "name", "description", "icon", "highlights"]
        read_only_fields = fields


class OrganizationBusinessTypeSerializer(serializers.ModelSerializer):
    """A business type as attached to one organization."""

    id = serializers.PrimaryKeyRelatedField(source="business_type", read_only=True)
    code = serializers.ReadOnlyField(source="business_type.code")
    name = serializers.ReadOnlyField(source="business_type.name")

    class Meta:
        model = OrganizationBusinessType
        fields = ["id", "code", "name", "is_primary"]
        read_only_fields = fields


class OrganizationBusinessTypesUpdateSerializer(serializers.Serializer):
    """
    Body of PUT /organizations/{id}/business-types/ (replaces the whole set).
        {"business_types": ["retail", "wholesale_distribution"],
         "primary_business_type": "retail"}
    """

    business_types = serializers.SlugRelatedField(
        many=True,
        slug_field="code",
        queryset=BusinessType.objects.active(),
        allow_empty=False,
    )
    primary_business_type = serializers.SlugRelatedField(
        slug_field="code",
        queryset=BusinessType.objects.active(),
    )

    def validate(self, attrs):
        selected = {bt.pk for bt in attrs["business_types"]}
        if len(selected) != len(attrs["business_types"]):
            raise serializers.ValidationError({"business_types": _("Duplicate business types are not allowed.")})
        if attrs["primary_business_type"].pk not in selected:
            raise serializers.ValidationError(
                {"primary_business_type": _("The primary business type must be one of the selected types.")}
            )
        return attrs


# ===========================================================================
# 2. Organization
# ===========================================================================
class OrganizationProfileValidationMixin:
    """
    Validation shared by organization create and update.

    Standalone values are checked in field-level hooks. State and phone depend
    on the organization's country (a sibling field on create, the stored value
    on update), so they are checked in validate().

    DRF's CharField already trims surrounding whitespace, so these hooks only
    normalise and validate.
    """

    def validate_country(self, value: str) -> str:
        value = value.upper()
        if not reference_data.is_valid_country(value):
            raise serializers.ValidationError(_("Select a valid country."))
        return value

    def validate_currency(self, value: str) -> str:
        value = value.upper()
        if not reference_data.is_valid_currency(value):
            raise serializers.ValidationError(_("Select a valid currency."))
        return value

    def validate_timezone(self, value: str) -> str:
        if value and not reference_data.is_valid_timezone(value):
            raise serializers.ValidationError(_("Select a valid timezone."))
        return value

    def validate_registration_number(self, value: str) -> str:
        # Optional (CAC/RC). Normalise only; verifying against CAC is out of scope.
        value = re.sub(r"\s+", "", value).upper()
        if value and not REGISTRATION_NUMBER_RE.fullmatch(value):
            raise serializers.ValidationError(_("Enter a valid registration number."))
        return value

    def validate_postal_code(self, value: str) -> str:
        return " ".join(value.upper().split())

    # -- country-dependent fields -------------------------------------------
    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        attrs = super().validate(attrs)
        country = self._resolve_country(attrs)
        errors: dict[str, Any] = {}

        if "state" in attrs:
            try:
                attrs["state"] = self._clean_state(attrs["state"], country)
            except serializers.ValidationError as exc:
                errors["state"] = exc.detail

        if attrs.get("phone"):
            try:
                attrs["phone"] = self._clean_phone(attrs["phone"], country)
            except serializers.ValidationError as exc:
                errors["phone"] = exc.detail

        if errors:
            raise serializers.ValidationError(errors)
        return attrs

    def _resolve_country(self, attrs: dict[str, Any]) -> str:
        """Country from this request if present, else the stored one (PATCH)."""
        if attrs.get("country"):
            return attrs["country"]
        instance = getattr(self, "instance", None)
        return instance.country if instance is not None and instance.country else ""

    @staticmethod
    def _clean_state(value: str, country: str) -> str:
        """Match against the State table, returning the canonical spelling."""
        if not country:
            return value
        names = State.objects.filter(country=country).values_list("name", flat=True)
        canonical_by_key = {name.casefold(): name for name in names}
        if not canonical_by_key:
            # Country not seeded yet: accept free text instead of blocking onboarding.
            return value
        canonical = canonical_by_key.get(value.casefold())
        if canonical is None:
            raise serializers.ValidationError(_("Select a valid state or region."))
        return canonical

    @staticmethod
    def _clean_phone(value: str, country: str) -> str:
        """
        Normalise to E.164 (+2348031234567) using libphonenumber. National
        formats (0803..., 803...) are completed with the organization's
        country; with no country the number must start with "+" or "00".
        """
        if value.startswith("00"):
            value = f"+{value[2:]}"
        try:
            parsed = phonenumbers.parse(value, country or None)
        except phonenumbers.NumberParseException as exc:
            raise serializers.ValidationError(_("Enter a valid phone number.")) from exc
        if not phonenumbers.is_valid_number(parsed):
            raise serializers.ValidationError(_("Enter a valid phone number."))
        return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)

class OrganizationSerializer(serializers.ModelSerializer):
    """
    Read serializer. Load through Organization.objects.for_user(user).with_detail()
    so plan and business types are preloaded. Members are intentionally NOT
    embedded: use the members action.
    """

    plan = PlanSerializer(source="current_plan", read_only=True)
    business_types = OrganizationBusinessTypeSerializer(
        source="organization_business_types", many=True, read_only=True
    )
    primary_business_type = serializers.SerializerMethodField()

    class Meta:
        model = Organization
        fields = [
            "id",
            "name",
            "legal_name",
            "business_types",
            "primary_business_type",
            "registration_number",
            "state",
            "address",
            "phone",
            "email",
            "staff_size",
            "country",
            "currency",
            "timezone",
            "domain",
            "logo",
            "plan",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_primary_business_type(self, obj):
        return next(
            (link.business_type.code for link in obj.organization_business_types.all() if link.is_primary),
            None,
        )


class OrganizationCreateSerializer(
    OrganizationProfileValidationMixin,
    serializers.ModelSerializer,
):
    """
    Body of POST /organizations/: onboarding screen 1 (business type) and
    screen 2 (business details) submitted together, so no half-created
    organization can exist.

    Branding and templates belong to a later onboarding step.

    Timezone is not collected from the onboarding request. The service layer
    applies the default timezone of Africa/Lagos through OrganizationProfile.
    """

    business_type = serializers.SlugRelatedField(
        slug_field="code",
        queryset=BusinessType.objects.active(),
        write_only=True,
    )

    class Meta:
        model = Organization
        fields = [
            "id",
            "business_type",
            "name",
            "registration_number",  # CAC/RC (Optional)
            "country",
            "state",
            "address",
            "postal_code",
            "tax_id",
            "currency",
            "phone",
            "staff_size",
        ]

        read_only_fields = ["id"]

        extra_kwargs = {
            "name": {
                "required": True,
                "allow_blank": False,
            },
            "registration_number": {
                "required": False,
            },
            # The model allows blank; the onboarding form does not.
            "country": {
                "required": True,
                "allow_blank": False,
            },
            "currency": {
                "required": True,
                "allow_blank": False,
            },
            "state": {
                "required": True,
                "allow_blank": False,
            },
            "address": {
                "required": True,
                "allow_blank": False,
            },
            "staff_size": {
                "required": True,
                "allow_blank": False,
            },
            # Not collected on the business-details screen.
            # The service layer applies the default timezone.
            "postal_code": {
                "required": False,
            },
            "tax_id": {
                "required": False,
            },
            "phone": {
                "required": True,
                "allow_blank": False,
            },
        }

    def create(self, validated_data):
        business_type = validated_data.pop("business_type")
        user = self.context["request"].user

        organization = services.create_organization(
            user=user,
            business_type=business_type,
            profile=services.OrganizationProfile(**validated_data),
        )

        # Reload through the tenant-scoped queryset so the response
        # is fully preloaded.
        return (
            Organization.objects
            .for_user(user)
            .with_detail()
            .get(pk=organization.pk)
        )

    def to_representation(self, instance):
        return OrganizationSerializer(
            instance,
            context=self.context,
        ).data



class OrganizationUpdateSerializer(OrganizationProfileValidationMixin, serializers.ModelSerializer):
    """
    Body of PUT/PATCH /organizations/{id}/ (profile + branding). Business types
    have their own action.

    country and currency are deliberately not editable here: they are fixed at
    creation, because changing currency after transactions exist would rewrite
    the meaning of historical records. State and phone are still validated
    against the organization's stored country (see the validation mixin).

    Template fields are generated from the model FKs (so `limit_choices_to` on
    type/active still applies) and re-scoped per organization in get_fields():
    shared defaults + this organization's own templates. Without that, any
    organization could assign another tenant's custom template by primary key.
    """

    TEMPLATE_FIELDS = {
        "invoice_template": "invoice",
        "receipt_template": "receipt",
        "quote_template": "quote",
        "credit_note_template": "credit_note",
    }

    class Meta:
        model = Organization
        fields = [
            "name",
            "legal_name",
            "registration_number",
            "tax_id",
            "state",
            "address",
            "postal_code",
            "phone",
            "email",
            "staff_size",
            "timezone",
            "domain",
            "logo",
            "header_text",
            "footer_text",
            "invoice_template",
            "receipt_template",
            "quote_template",
            "credit_note_template",
        ]
        extra_kwargs = {
            # Onboarding requires these; editing must not be a way to blank them.
            "state": {"allow_blank": False},
            "address": {"allow_blank": False},
        }

    def get_fields(self):
        fields = super().get_fields()
        # self.instance is None only during schema generation -> shared templates.
        for name, template_type in self.TEMPLATE_FIELDS.items():
            fields[name].queryset = document_services.templates_available_to(self.instance, template_type)
        return fields

    def validate(self, attrs):
        # Country-dependent checks (state, phone) live in the mixin. Without
        # this call they are silently skipped on PUT/PATCH.
        attrs = super().validate(attrs)

        # Custom (organization-owned) templates are a paid capability (PRD §10, §25).
        for name in self.TEMPLATE_FIELDS:
            template = attrs.get(name)
            if (
                template is not None
                and template.organization_id is not None
                and not document_services.can_use_custom_templates(self.instance)
            ):
                raise serializers.ValidationError({name: _("Custom templates are not available on your current plan.")})
        return attrs

    def to_representation(self, instance):
        # Respond with the full organization, not just the fields that were editable.
        return OrganizationSerializer(instance, context=self.context).data


# ===========================================================================
# 3. Members & roles  (organization actions)
# ===========================================================================
class OrganizationMemberSerializer(serializers.ModelSerializer):
    """
    One row of GET /organizations/{id}/members/. Pending invitations have no
    user yet, so `user` falls back to the invited email; `accepted` tells them
    apart. Load rows with Membership.objects.with_user_and_role().
    """

    user = serializers.SerializerMethodField(
        help_text="Email of the member, or the invited email for a pending invite"
    )
    role = serializers.CharField(
        source="role.name", read_only=True, help_text="Role name, e.g. Administrator"
    )
    active = serializers.BooleanField(source="is_active", read_only=True)
    joined_at = serializers.DateTimeField(source="created_at", read_only=True)

    class Meta:
        model = Membership
        fields = ["id", "user", "role", "active", "joined_at"]
        read_only_fields = fields

    @extend_schema_field(serializers.EmailField())
    def get_user(self, obj):
        return obj.user.email if obj.user_id else obj.invited_email


class RoleSerializer(serializers.ModelSerializer):
    """Roles an organization can assign (feeds the invite-team role picker)."""

    class Meta:
        model = Role
        fields = ["id", "name", "slug", "description", "is_system"]
        read_only_fields = fields


# ===========================================================================
# 4. Memberships
# ===========================================================================
class MembershipSerializer(serializers.ModelSerializer):
    """
    Membership detail (read-only). Every row is a real member; pending
    invitations are a separate resource. Load with
    Membership.objects.with_user_and_role().select_related("organization").
    """

    user_name = serializers.CharField(source="user.name", read_only=True)
    user_email = serializers.EmailField(source="user.email", read_only=True)
    organization_name = serializers.CharField(source="organization.name", read_only=True)
    role_name = serializers.CharField(source="role.name", read_only=True)
    role_slug = serializers.CharField(source="role.slug", read_only=True)

    class Meta:
        model = Membership
        fields = [
            "id",
            "user",
            "user_name",
            "user_email",
            "organization",
            "organization_name",
            "role",
            "role_name",
            "role_slug",
            "is_active",
            "created_at",
        ]
        read_only_fields = fields


class MembershipUpdateSerializer(serializers.ModelSerializer):
    """
    Body of PUT/PATCH /memberships/{id}/: change a member's role and/or active
    flag. Who may change whom is enforced in services.update_membership.
    (Unchanged: `role` and `is_active` still exist on Membership.)
    """

    class Meta:
        model = Membership
        fields = ["role", "is_active"]

    def get_fields(self):
        fields = super().get_fields()
        # Only roles of the membership's own organization can be named.
        organization = self.instance.organization if isinstance(self.instance, Membership) else None
        fields["role"].queryset = (
            Role.objects.for_organization(organization) if organization else Role.objects.none()
        )
        return fields

    def update(self, instance, validated_data):
        with domain_errors():
            return services.update_membership(
                membership=instance,
                actor=self.context["request"].user,
                role=validated_data.get("role"),
                is_active=validated_data.get("is_active"),
            )

    def to_representation(self, instance):
        return MembershipSerializer(instance, context=self.context).data


class AssignableRoleField(serializers.PrimaryKeyRelatedField):
    """
    A role the current caller may assign in the current organization.

    The queryset is resolved per request from the serializer context
    (`organization`, `actor_membership`), using the same function that feeds
    the "Assign Role" dropdown. Without that context nothing is assignable.
    """

    default_error_messages = {
        "does_not_exist": "Select a valid role for this organization.",
        "incorrect_type": "Invalid role identifier.",
    }

    def get_queryset(self):
        organization = self.context.get("organization")
        actor_membership = self.context.get("actor_membership")
        if organization is None or actor_membership is None:
            return Role.objects.none()
        return assignable_roles(organization, actor_membership)


class InvitationCreateSerializer(serializers.Serializer):
    """
    Body of POST /organizations/{organization_id}/invitations/.

    The organization comes from the URL, never the body. The view supplies
    `organization` and `actor_membership` through the serializer context.
    """

    name = serializers.CharField(max_length=255, trim_whitespace=True)
    email = serializers.EmailField()
    role = AssignableRoleField()

    def validate_email(self, value: str) -> str:
        return value.strip().lower()


class InvitationSerializer(serializers.ModelSerializer):
    """Staff-side list/detail. Never exposes the token or its hash."""

    role_name = serializers.CharField(source="role.name", read_only=True)
    invited_by_name = serializers.CharField(source="invited_by.name", read_only=True, allow_null=True)
    status = serializers.CharField(source="display_status", read_only=True)

    class Meta:
        model = Invitation
        fields = [
            "id", "name", "email", "role", "role_name", "status",
            "expires_at", "last_sent_at", "invited_by_name",
            "accepted_at", "revoked_at", "created_at",
        ]
        read_only_fields = fields


class InvitationRevokeSerializer(serializers.Serializer):
    """Optional body of POST .../invitations/{id}/revoke/; the reason goes to the audit log."""

    reason = serializers.CharField(max_length=500, required=False, allow_blank=True, default="")


class RoleOptionSerializer(serializers.ModelSerializer):
    """Feeds the "Assign Role" dropdown. Use with `assignable_roles()`."""

    class Meta:
        model = Role
        fields = ["id", "name", "slug", "description", "is_system"]
        read_only_fields = fields


# ===========================================================================
# Invitations: public side (token holder)
# ===========================================================================
class InvitationPreviewSerializer(serializers.ModelSerializer):
    """
    Public preview shown before the invitee signs up or logs in. Deliberately
    minimal: no ids, no token, no other members, and a masked email.
    """

    organization = serializers.CharField(source="organization.name", read_only=True)
    role = serializers.CharField(source="role.name", read_only=True)
    invited_email = serializers.SerializerMethodField()

    class Meta:
        model = Invitation
        fields = ["organization", "role", "name", "invited_email", "expires_at"]
        read_only_fields = fields

    @extend_schema_field(serializers.CharField())
    def get_invited_email(self, obj):
        return mask_email(obj.email)


class AcceptInvitationSerializer(serializers.Serializer):
    """
    Body of POST /invitations/accept/. Shape only: every rule (verified email,
    matching address, expiry, seats) lives in services.accept_invitation.
    """

    token = serializers.RegexField(
        r"^[\w-]{20,128}$",
        help_text="The invitation token from the email link.",
        error_messages={"invalid": "Invalid invitation token."},
    )


class InvitationAcceptedSerializer(serializers.ModelSerializer):
    """Response of a successful accept: what the frontend needs to switch organization."""

    membership_id = serializers.ReadOnlyField(source="id")
    organization_id = serializers.ReadOnlyField()
    organization_name = serializers.CharField(source="organization.name", read_only=True)
    role = serializers.CharField(source="role.name", read_only=True)

    class Meta:
        model = Membership
        fields = ["membership_id", "organization_id", "organization_name", "role"]
        read_only_fields = fields


class ErrorSerializer(serializers.Serializer):
    """Shape of every business-rule error (PRD §46). Used for API docs only."""

    code = serializers.CharField()
    detail = serializers.CharField()

class OptionSerializer(serializers.Serializer):
    code = serializers.CharField()
    name = serializers.CharField()


class StateOptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = State
        fields = ["name", "code"]


class StatesQuerySerializer(serializers.Serializer):
    country = serializers.CharField(min_length=2, max_length=2)

    def validate_country(self, value: str) -> str:
        value = value.upper()
        if not reference_data.is_valid_country(value):
            msg = "Unknown country code."
            raise serializers.ValidationError(msg)
        return value

class ValidationErrorSerializer(serializers.Serializer):
    """Shape of a field-validation error (400). Used for API docs only."""

    code = serializers.CharField(help_text="Always VALIDATION_ERROR.")
    detail = serializers.CharField(help_text="Always 'Invalid input.'")
    errors = serializers.DictField(
        child=serializers.ListField(child=serializers.CharField()),
        help_text="Field name mapped to its list of error messages.",
    )
