import re
from typing import Any

import phonenumbers
from django.db.models import Model
from django.utils.translation import gettext_lazy as _
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from core.applications.invoice.services import documents as document_services
from core.applications.notification.audit.context import AuditContext
from core.applications.subscriptions.models import Plan
from core.applications.users import reference_data
from core.applications.users import services
from core.applications.users.errors import domain_errors
from core.applications.users.invitation_services import assignable_locations
from core.applications.users.invitation_services import assignable_roles
from core.applications.users.invitation_services import mask_email
from core.applications.users.models import BusinessType
from core.applications.users.models import Invitation
from core.applications.users.models import Membership
from core.applications.users.models import Organization
from core.applications.users.models import OrganizationBusinessType
from core.applications.users.models import Role
from core.applications.users.models import State
from core.applications.users.models import User
from core.applications.warehouse.models import Warehouse

REGISTRATION_NUMBER_RE = re.compile(r"[A-Z0-9\-/]{3,30}")


# ===========================================================================
# 1. Plans and business types
# ===========================================================================
class PlanSerializer(serializers.ModelSerializer):
    """The subscription plan linked to an organization."""

    class Meta:
        model = Plan
        fields = ["id", "name", "description", "is_active"]


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
        help_text="Codes of every business type the organization should have.",
    )
    primary_business_type = serializers.SlugRelatedField(
        slug_field="code",
        queryset=BusinessType.objects.active(),
        help_text="One of the codes in business_types. Drives terminology and dashboard defaults.",
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
        value = re.sub(r"\s+", "", value or "").upper()
        if value and not REGISTRATION_NUMBER_RE.fullmatch(value):
            raise serializers.ValidationError(_("Enter a valid registration number."))
        return value

    def validate_postal_code(self, value: str | None) -> str:
        # The model allows NULL, and DRF still calls this hook with None.
        return " ".join(value.upper().split()) if value else ""

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
    primary_business_type = serializers.SerializerMethodField(
        help_text="Code of the primary business type, e.g. 'retail'."
    )

    class Meta:
        model = Organization
        fields = [
            "id",
            "name",
            "legal_name",
            "business_types",
            "primary_business_type",
            "registration_number",
            "default_tax_rate",
            "prices_include_tax",
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


class OrganizationCreateSerializer(OrganizationProfileValidationMixin, serializers.ModelSerializer):
    """
    Body of POST /organizations/: onboarding screen 1 (business type) and
    screen 2 (business details) submitted together, so no half-created
    organization can exist. The service also creates the default location.

    Branding and templates belong to a later onboarding step. Timezone is not
    collected here: the service layer applies the default (Africa/Lagos).
    """

    business_type = serializers.SlugRelatedField(
        slug_field="code",
        queryset=BusinessType.objects.active(),
        write_only=True,
        help_text="Code from GET /business-type/?context=onboarding. Becomes the primary business type.",
    )

    class Meta:
        model = Organization
        fields = [
            "id",
            "business_type",
            "name",
            "registration_number",  # CAC/RC, optional
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
        # The model allows blank for most of these; the onboarding form does not.
        extra_kwargs = {
            "name": {"required": True, "allow_blank": False},
            "registration_number": {"required": False},
            "country": {"required": True, "allow_blank": False},
            "currency": {"required": True, "allow_blank": False},
            "state": {"required": True, "allow_blank": False},
            "address": {"required": True, "allow_blank": False},
            "staff_size": {"required": True, "allow_blank": False},
            "phone": {"required": True, "allow_blank": False},
            "postal_code": {"required": False},
            "tax_id": {"required": False},
        }

    def create(self, validated_data):
        business_type = validated_data.pop("business_type")
        request = self.context["request"]

        with domain_errors():
            organization = services.create_organization(
                user=request.user,
                business_type=business_type,
                profile=services.OrganizationProfile(**validated_data),
                audit_context=AuditContext.from_request(request),
            )

        # Reload through the tenant-scoped queryset so the response is fully preloaded.
        return Organization.objects.for_user(request.user).with_detail().get(pk=organization.pk)

    def to_representation(self, instance):
        return OrganizationSerializer(instance, context=self.context).data


class OrganizationUpdateSerializer(OrganizationProfileValidationMixin, serializers.ModelSerializer):
    """
    Body of PUT/PATCH /organizations/{id}/ (profile and branding). Business
    types have their own action.

    country and currency are deliberately not editable: they are fixed at
    creation, because changing currency after transactions exist would rewrite
    the meaning of historical records. State and phone are still validated
    against the organization's stored country (see the validation mixin).

    Template fields are generated from the model FKs (so `limit_choices_to`
    still applies) and re-scoped per organization in get_fields(): shared
    defaults plus this organization's own templates. Without that, any
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
            # Tax settings (products.tax). Affect new sales and invoices only;
            # existing documents keep the setting they were created with.
            "default_tax_rate",
            "prices_include_tax",
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
# 3. Locations (shared by invitations and memberships)
# ===========================================================================
class LocationOptionSerializer(serializers.ModelSerializer):
    """
    One location, as shown in dropdowns and inside invitations and memberships.
    Use with `assignable_locations()` for the "Assign Location" dropdown.
    """

    class Meta:
        model = Warehouse
        fields = ["id", "name", "code", "location_type", "is_default"]
        read_only_fields = fields


class ContextScopedRelatedField(serializers.PrimaryKeyRelatedField):
    """
    Base for related fields whose valid choices depend on who is asking and in
    which organization.

    Reads `organization` and `actor_membership` from the serializer context
    (the views supply both). Without them nothing is selectable, so a
    misconfigured view fails closed instead of exposing every row. An id the
    caller may not use fails with DRF's normal "does not exist" error, so the
    API never confirms that a row exists elsewhere.

    Subclasses set `model` and implement `choices_for`.
    """

    model: type[Model]

    def choices_for(self, organization, actor_membership):
        raise NotImplementedError

    def get_queryset(self):
        organization = self.context.get("organization")
        actor_membership = self.context.get("actor_membership")
        if organization is None or actor_membership is None:
            return self.model.objects.none()
        return self.choices_for(organization, actor_membership)


class AssignableRoleField(ContextScopedRelatedField):
    """A role the current caller may assign in the current organization."""

    model = Role
    default_error_messages = {
        "does_not_exist": "Select a valid role for this organization.",
        "incorrect_type": "Invalid role identifier.",
    }

    def choices_for(self, organization, actor_membership):
        return assignable_roles(organization, actor_membership)

@extend_schema_field(OpenApiTypes.INT)
class AssignableLocationField(ContextScopedRelatedField):
    """A location the current caller may assign in the current organization."""

    model = Warehouse
    default_error_messages = {
        "does_not_exist": "Select a valid location for this organization.",
        "incorrect_type": "Invalid location identifier.",
    }

    def choices_for(self, organization, actor_membership):
        return assignable_locations(organization, actor_membership)


class LocationScopeMixin(serializers.Serializer):
    """
    Where a person may work: every location, or a chosen list. Exactly one of
    the two must be given. Shared by invitations and membership changes so the
    rule cannot drift between them.
    """

    all_locations = serializers.BooleanField(
        default=False,
        help_text="true = access to every location, including ones added later.",
    )
    locations = AssignableLocationField(
        many=True,
        required=False,
        allow_empty=True,
        help_text=(
            "Location ids from GET /organizations/{id}/invitations/assignable-locations/. "
            "Required when all_locations is false; leave empty when it is true."
        ),
    )

    def validate(self, attrs):
        attrs = super().validate(attrs)
        has_locations = bool(attrs.get("locations"))

        if attrs["all_locations"] and has_locations:
            raise serializers.ValidationError(
                {"locations": "Send either all_locations or a list of locations, not both."}
            )
        if not attrs["all_locations"] and not has_locations:
            raise serializers.ValidationError(
                {"locations": "Choose at least one location, or set all_locations."}
            )
        return attrs


class MembershipLocationsSerializer(LocationScopeMixin):
    """
    Body of PUT /memberships/{id}/locations/.

    Context required: `organization` (the membership's organization) and
    `actor_membership` (the caller's membership there).
    """


# ===========================================================================
# 4. Members and roles  (organization actions)
# ===========================================================================
class OrganizationMemberSerializer(serializers.ModelSerializer):
    """
    One row of GET /organizations/{id}/members/. Every row is a real member:
    pending invitations are a separate resource. Load rows with
    Membership.objects.with_user_and_role().prefetch_related("locations").
    """

    user = serializers.EmailField(source="user.email", read_only=True, help_text="The member's email.")
    user_name = serializers.CharField(source="user.name", read_only=True, help_text="The member's full name.")
    role = serializers.CharField(source="role.name", read_only=True, help_text="Role name, e.g. Administrator.")
    active = serializers.BooleanField(
        source="is_active", read_only=True, help_text="false = removed or suspended; has no access."
    )
    joined_at = serializers.DateTimeField(source="created_at", read_only=True)
    locations = LocationOptionSerializer(
        many=True,
        read_only=True,
        help_text="Locations the member is limited to. Empty when all_locations is true.",
    )

    class Meta:
        model = Membership
        fields = ["id", "user", "user_name", "role", "active", "joined_at", "all_locations", "locations"]
        read_only_fields = fields


class RoleSerializer(serializers.ModelSerializer):
    """A role an organization can assign (built-in or custom)."""

    class Meta:
        model = Role
        fields = ["id", "name", "slug", "description", "is_system"]
        read_only_fields = fields


class RoleOptionSerializer(RoleSerializer):
    """Feeds the "Assign Role" dropdown. Use with `assignable_roles()`."""


# ===========================================================================
# 5. Memberships
# ===========================================================================
class MembershipSerializer(serializers.ModelSerializer):
    """
    Membership detail (read-only). Every row is a real member; pending
    invitations are a separate resource. Load with
    Membership.objects.with_user_and_role().select_related("organization")
    .prefetch_related("locations").
    """

    user_name = serializers.CharField(source="user.name", read_only=True)
    user_email = serializers.EmailField(source="user.email", read_only=True)
    organization_name = serializers.CharField(source="organization.name", read_only=True)
    role_name = serializers.CharField(source="role.name", read_only=True)
    role_slug = serializers.CharField(source="role.slug", read_only=True)
    locations = LocationOptionSerializer(
        many=True,
        read_only=True,
        help_text="Locations the member is limited to. Empty when all_locations is true.",
    )

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
            "all_locations",
            "locations",
            "created_at",
        ]
        read_only_fields = fields


class MembershipUpdateSerializer(serializers.ModelSerializer):
    """
    Body of PUT/PATCH /memberships/{id}/: change a member's role and/or active
    flag. Who may change whom is enforced in services.update_membership.
    Location access has its own endpoint (MembershipLocationsSerializer).
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
                audit_context=AuditContext.from_request(self.context["request"]),
            )

    def to_representation(self, instance):
        return MembershipSerializer(instance, context=self.context).data


# ===========================================================================
# 6. Invitations: team side
# ===========================================================================
class InvitationCreateSerializer(LocationScopeMixin):
    """
    Body of POST /organizations/{organization_id}/invitations/.

    The organization comes from the URL, never the body. The view supplies
    `organization` and `actor_membership` through the serializer context.
    """

    name = serializers.CharField(
        max_length=255,
        trim_whitespace=True,
        required=False,
        allow_blank=True,
        allow_null=True,
        default="",
        help_text="Optional. The invitee's display name; an empty string when omitted.",
    )
    email = serializers.EmailField(help_text="Lower-cased and trimmed. The invitee must sign in with this address.")
    role = AssignableRoleField(help_text="A role id from GET .../invitations/assignable-roles/.")

    def validate_name(self, value: str | None) -> str:
        # DRF still calls this hook with None when the client sends "name": null.
        return (value or "").strip()

    def validate_email(self, value: str) -> str:
        return value.strip().lower()


class InvitationSerializer(serializers.ModelSerializer):
    """
    Staff-side list/detail. Never exposes the token or its hash. Load with
    select_related("role", "invited_by").prefetch_related("locations").
    """

    role_name = serializers.CharField(source="role.name", read_only=True)
    invited_by_name = serializers.CharField(source="invited_by.name", read_only=True, allow_null=True)
    status = serializers.CharField(
        source="display_status",
        read_only=True,
        help_text="pending, accepted, revoked or expired (derived from expires_at).",
    )
    locations = LocationOptionSerializer(
        many=True,
        read_only=True,
        help_text="Locations the invitee is limited to. Empty when all_locations is true.",
    )

    class Meta:
        model = Invitation
        fields = [
            "id",
            "name",
            "email",
            "role",
            "role_name",
            "status",
            "all_locations",
            "locations",
            "expires_at",
            "last_sent_at",
            "invited_by_name",
            "accepted_at",
            "revoked_at",
            "created_at",
        ]
        read_only_fields = fields


class InvitationRevokeSerializer(serializers.Serializer):
    """Optional body of POST .../invitations/{id}/revoke/; the reason goes to the audit log only."""

    reason = serializers.CharField(max_length=500, required=False, allow_blank=True, default="")


# ===========================================================================
# 7. Invitations: public side (token holder)
# ===========================================================================
class InvitationPreviewSerializer(serializers.ModelSerializer):
    """
    Public preview shown before the invitee signs up or logs in. Deliberately
    minimal: no ids, no token, no other members, a masked email and location
    names only.
    """

    organization = serializers.CharField(source="organization.name", read_only=True)
    role = serializers.CharField(source="role.name", read_only=True)
    invited_email = serializers.SerializerMethodField(help_text="Masked, e.g. a***@example.com.")
    account_exists = serializers.SerializerMethodField(
        help_text="true = the invited email already has an account: send the user to log in, otherwise to sign up."
    )
    locations = serializers.SerializerMethodField(
        help_text="Names of the locations the invitee is limited to. Empty when all_locations is true."
    )

    class Meta:
        model = Invitation
        fields = [
            "organization",
            "role",
            "name",
            "invited_email",
            "account_exists",
            "all_locations",
            "locations",
            "expires_at",
        ]
        read_only_fields = fields

    @extend_schema_field(serializers.CharField())
    def get_invited_email(self, obj):
        return mask_email(obj.email)

    @extend_schema_field(serializers.BooleanField())
    def get_account_exists(self, obj):
        # Safe to reveal: the caller already holds the single-use token that was emailed to this address.
        return User.objects.filter(email__iexact=obj.email).exists()

    @extend_schema_field(serializers.ListField(child=serializers.CharField()))
    def get_locations(self, obj):
        return [location.name for location in obj.locations.all()]


class AcceptInvitationSerializer(serializers.Serializer):
    """
    Body of POST /invitations/accept/ and /invitations/validate/. Shape only:
    every rule (verified email, matching address, expiry, seats) lives in the
    invitation services.
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
    locations = LocationOptionSerializer(
        many=True,
        read_only=True,
        help_text="Locations the new member is limited to. Empty when all_locations is true.",
    )

    class Meta:
        model = Membership
        fields = ["membership_id", "organization_id", "organization_name", "role", "all_locations", "locations"]
        read_only_fields = fields


# ===========================================================================
# 8. Reference data and API-documentation helpers
# ===========================================================================
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


class ErrorSerializer(serializers.Serializer):
    """Shape of every business-rule error (PRD §46). Used for API docs only."""

    code = serializers.CharField()
    detail = serializers.CharField()


class ValidationErrorSerializer(serializers.Serializer):
    """Shape of a field-validation error (400). Used for API docs only."""

    code = serializers.CharField(help_text="Always VALIDATION_ERROR.")
    detail = serializers.CharField(help_text="Always 'Invalid input.'")
    errors = serializers.DictField(
        child=serializers.ListField(child=serializers.CharField()),
        help_text="Field name mapped to its list of error messages.",
    )
