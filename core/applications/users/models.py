import uuid
from typing import ClassVar

import auto_prefetch
from django.contrib.auth.models import AbstractUser
from django.db.models import CASCADE
from django.db.models import PROTECT
from django.db.models import SET_NULL
from django.db.models import BooleanField
from django.db.models import CharField
from django.db.models import DateTimeField
from django.db.models import EmailField
from django.db.models import ImageField
from django.db.models import Index
from django.db.models import JSONField
from django.db.models import ManyToManyField
from django.db.models import Model
from django.db.models import OneToOneField
from django.db.models import PositiveSmallIntegerField
from django.db.models import Q
from django.db.models import SlugField
from django.db.models import TextField
from django.db.models import UniqueConstraint
from django.db.models import UUIDField
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from core.applications.users.queryset import BusinessTypeManager
from core.applications.users.queryset import MembershipManager
from core.applications.users.queryset import OrganizationBusinessTypeManager
from core.applications.users.queryset import OrganizationManager
from core.applications.users.queryset import RoleManager
from core.helper.enums import StaffSizeChoices
from core.helper.media import MediaHelper
from core.helper.models import TimeBasedModel
from core.helper.utils import default_invite_expiry

from .managers import UserManager


class Permission(TimeBasedModel):
    """
    A single grantable capability in the system (e.g. VIEW_PRODUCT_COSTS,
    EDIT_INVENTORY, CANCEL_INVOICE). This is a fixed, system-wide catalog —
    not organization-scoped — since the capabilities the platform exposes
    are the same for every tenant. What varies per organization is which
    Roles are granted which Permissions.
    """

    code = CharField(max_length=100, unique=True, help_text=_("e.g. 'EDIT_INVENTORY'."))
    name = CharField(max_length=150)
    module = CharField(
        max_length=50,
        blank=True,
        help_text=_("Module this permission belongs to, e.g. 'inventory', 'invoices'."),
    )
    description = TextField(blank=True, null=True)

    class Meta(auto_prefetch.Model.Meta):
        verbose_name = _("Permission")
        verbose_name_plural = _("Permissions")
        ordering = ["module", "name"]

    def __str__(self):
        return self.code


class Role(TimeBasedModel):
    """
    A role belongs to exactly one organization, keeping every role
    (including the built-in defaults) inside that tenant's boundary per the
    platform's strict isolation rule. When an organization is created, the
    application layer seeds it with the default roles from the PRD (Owner,
    Administrator, Manager, Sales Manager, Sales Representative, Inventory
    Manager, Inventory Staff, Accountant, Finance Officer, Teacher/Staff,
    Front Desk). `is_system` marks those defaults so they can't be renamed
    or deleted, while still allowing their permission set to be edited.
    Organizations can additionally create fully custom roles.
    """

    organization = auto_prefetch.ForeignKey(
        "users.Organization",
        on_delete=CASCADE,
        related_name="roles",
    )
    name = CharField(max_length=100)
    slug = CharField(max_length=100, help_text=_("Stable key, e.g. 'sales-representative'."))
    description = TextField(blank=True, null=True)
    is_system = BooleanField(
        default=False,
        help_text=_("True for the PRD's default roles; protects name/slug from editing."),
    )
    permissions = ManyToManyField(
        "users.Permission",
        through="users.RolePermission",
        related_name="roles",
        blank=True,
    )
    objects = RoleManager()

    class Meta(auto_prefetch.Model.Meta):
        verbose_name = _("Role")
        verbose_name_plural = _("Roles")
        ordering = ["organization", "name"]
        constraints: ClassVar = [
            UniqueConstraint(fields=["organization", "slug"], name="unique_role_slug_per_org"),
        ]

    def __str__(self):
        return f"{self.name} ({self.organization.name})"


class RolePermission(TimeBasedModel):
    role = auto_prefetch.ForeignKey("users.Role", on_delete=CASCADE, related_name="role_permissions")
    permission = auto_prefetch.ForeignKey(
        "users.Permission",
        on_delete=CASCADE,
        related_name="permission_roles",
    )

    class Meta(auto_prefetch.Model.Meta):
        verbose_name = _("Role Permission")
        verbose_name_plural = _("Role Permissions")
        constraints: ClassVar = [
            UniqueConstraint(fields=["role", "permission"], name="unique_role_permission"),
        ]

    def __str__(self):
        return f"{self.permission.code} on {self.role.name}"


class BusinessType(TimeBasedModel):
    """
    A business type is a high-level category of business, e.g. "Retail",
    "Wholesale", "Restaurant", "Service Provider". It is used to customize
    """

    code = SlugField(
        max_length=50, unique=True,
        help_text=_("Stable key, e.g. 'wholesale_distribution'."),
    )
    name = CharField(max_length=100)
    description = TextField(blank=True)
    icon = CharField(
        max_length=50,
        blank=True,
        help_text=_("Icon key resolved by the frontend, e.g. 'shopping-basket'."),
    )
    highlights = JSONField(
        default=list,
        blank=True,
        help_text=_("Short feature tags shown on the onboarding card."),
    )
    show_in_onboarding = BooleanField(default=True)
    is_active = BooleanField(default=True)
    sort_order = PositiveSmallIntegerField(default=0)

    objects = BusinessTypeManager()

    class Meta(auto_prefetch.Model.Meta):
        verbose_name = _("Business Type")
        verbose_name_plural = _("Business Types")
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name


class OrganizationBusinessType(TimeBasedModel):
    """
    Which business types an organization operates as. An organization has one
    or more; exactly one is primary and drives terminology/dashboard defaults.
    "At least one" is enforced in services.set_business_types (a DB constraint
    can't express it); "at most one primary" is enforced here.
    """

    organization = auto_prefetch.ForeignKey(
        "users.Organization",
        on_delete=CASCADE,
        related_name="organization_business_types",
    )
    business_type = auto_prefetch.ForeignKey(
        "users.BusinessType",
        on_delete=PROTECT,
        related_name="organization_links",
    )
    is_primary = BooleanField(default=False)
    objects = OrganizationBusinessTypeManager()

    class Meta(auto_prefetch.Model.Meta):
        verbose_name = _("Organization Business Type")
        verbose_name_plural = _("Organization Business Types")
        constraints: ClassVar = [
            UniqueConstraint(
                fields=["organization", "business_type"],
                name="unique_business_type_per_org",
            ),
            UniqueConstraint(
                fields=["organization"],
                condition=Q(is_primary=True),
                name="unique_primary_business_type_per_org",
            ),
        ]

    def __str__(self):
        suffix = " (primary)" if self.is_primary else ""
        return f"{self.organization.name}: {self.business_type.name}{suffix}"

class Organization(TimeBasedModel):
    """
    A tenant. Every organization-scoped record in the system must resolve
    back to one of these; this model itself is the root of that boundary.
    """

    # Identity
    name = CharField(max_length=255, help_text=_("Trading name."))
    legal_name = CharField(max_length=255, blank=True)
    business_types = ManyToManyField(
        "users.BusinessType",
        through="users.OrganizationBusinessType",
        related_name="organizations",
        blank=True,
    )
    staff_size = CharField(
        _("Number of staff"),
        max_length=20,
        choices=StaffSizeChoices.choices,
        blank=True,
    )
    domain = CharField(
        _("Custom Domain"),
        max_length=255,
        blank=True,
        null=True,
        help_text=_("Optional custom domain (e.g. acme.com)."),
    )
    created_by = auto_prefetch.ForeignKey(
        "users.User",
        on_delete=SET_NULL,
        null=True,
        blank=True,
        related_name="organizations_created",
        help_text=_("User who created this organization; becomes its Owner."),
    )

    # Contact
    email = EmailField(blank=True)
    phone = CharField(max_length=30, blank=True)
    address = TextField(blank=True)
    state = CharField(
        max_length=100,
        blank=True,
        help_text=_("State/province/region of the business address."),
    )

    # Locale & compliance
    country = CharField(max_length=2, blank=True, help_text=_("ISO 3166-1 alpha-2 country code."))
    currency = CharField(max_length=3, blank=True, help_text=_("ISO 4217 currency code."))
    timezone = CharField(max_length=64, blank=True)
    tax_id = CharField(max_length=100, blank=True)
    registration_number = CharField(max_length=100, blank=True)

    is_active = BooleanField(_("Active"), default=True)

    # Branding
    logo = ImageField(
        upload_to=MediaHelper.get_image_upload_path,
        blank=True,
        null=True,
        help_text=_("Logo to display on invoices and receipts."),
    )
    header_text = CharField(max_length=255, blank=True, null=True)
    footer_text = CharField(max_length=255, blank=True, null=True)

    # Template assignments
    invoice_template = auto_prefetch.ForeignKey(
        "invoice.DocumentTemplate",
        on_delete=SET_NULL,
        null=True,
        blank=True,
        related_name="invoice_organizations",
        limit_choices_to={"template_type": "invoice", "is_active": True},
    )
    receipt_template = auto_prefetch.ForeignKey(
        "invoice.DocumentTemplate",
        on_delete=SET_NULL,
        null=True,
        blank=True,
        related_name="receipt_organizations",
        limit_choices_to={"template_type": "receipt", "is_active": True},
    )
    quote_template = auto_prefetch.ForeignKey(
        "invoice.DocumentTemplate",
        on_delete=SET_NULL,
        null=True,
        blank=True,
        related_name="quote_organizations",
        limit_choices_to={"template_type": "quote", "is_active": True},
    )
    credit_note_template = auto_prefetch.ForeignKey(
        "invoice.DocumentTemplate",
        on_delete=SET_NULL,
        null=True,
        blank=True,
        related_name="credit_note_organizations",
        limit_choices_to={"template_type": "credit_note", "is_active": True},
    )
    postal_code = CharField(_("Postal/ZIP code"), max_length=20, blank=True, null=True)
    objects = OrganizationManager()

    class Meta(auto_prefetch.Model.Meta):
        verbose_name = _("Organization")
        verbose_name_plural = _("Organizations")
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def current_plan(self):
        subscription = getattr(self, "subscription", None)
        return subscription.plan if subscription else None

    @property
    def primary_business_type(self):
        """
        Return the primary business type for this organization, or None if not set.
        """
        for link in self.organization_business_types.all():
            if link.is_primary:
                return link.business_type
        return None

class User(AbstractUser):
    """
    Custom user model. Email is the unique identifier instead of username.
    Deliberately holds no organization- or role-specific data (no address,
    no department) — a single user can belong to many organizations with
    different roles, so that data lives on Membership / role-detail models
    instead, scoped correctly per organization.
    """

    first_name = None  # type: ignore
    last_name = None  # type: ignore
    username = None  # type: ignore

    name = CharField(_("Full Name"), max_length=255, blank=True)
    email = EmailField(_("Email Address"), unique=True, db_index=True)
    is_active = BooleanField(_("Active"), default=True)
    is_verified = BooleanField(_("Verified"), default=False)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects: ClassVar[UserManager] = UserManager()

    class Meta:
        verbose_name = _("User")
        verbose_name_plural = _("Users")
        ordering = ["-date_joined"]

    def __str__(self):
        return f"{self.name or self.email}"

    def get_absolute_url(self):
        return reverse("users:detail", kwargs={"pk": self.pk})

    @property
    def organizations(self):
        """Return all organizations the user is an accepted member of."""
        return Organization.objects.filter(memberships__user=self, memberships__accepted=True)


class Membership(TimeBasedModel):
    """
    Ties a User to an Organization with a specific Role. A pending
    invitation is a Membership row with `user` unset and `accepted=False`;
    it becomes a full membership once the invited person signs up/accepts.
    """

    user = auto_prefetch.ForeignKey(
        "users.User",
        on_delete=CASCADE,
        related_name="memberships",
        null=True,
        blank=True,
    )
    organization = auto_prefetch.ForeignKey(
        "users.Organization",
        on_delete=CASCADE,
        related_name="memberships",
    )
    role = auto_prefetch.ForeignKey(
        "users.Role",
        on_delete=PROTECT,
        related_name="memberships",
        help_text=_("Must belong to the same organization as this membership."),
    )
    is_active = BooleanField(default=True)

    # Invitation fields
    invited_email = EmailField(_("Invited Email"), null=True, blank=True)
    invite_token = UUIDField(default=uuid.uuid4, unique=True, editable=False)
    accepted = BooleanField(default=False)
    expires_at = DateTimeField(default=default_invite_expiry)
    objects = MembershipManager()
    class Meta(auto_prefetch.Model.Meta):
        verbose_name = _("Membership")
        verbose_name_plural = _("Memberships")
        ordering = ["organization", "-created_at"]
        constraints: ClassVar = [
            UniqueConstraint(fields=["user", "organization"], name="unique_membership_per_user_org"),
            UniqueConstraint(
                fields=["organization", "invited_email"],
                name="unique_pending_invite_per_org_email",
                condition=Q(accepted=False, invited_email__isnull=False),
            ),
        ]
        indexes = [
            Index(fields=["organization", "role"]),
        ]

    def __str__(self):
        if self.user:
            return f"{self.user.email} in {self.organization.name} as {self.role.name}"
        return f"Invitation for {self.invited_email} to {self.organization.name} as {self.role.name}"




class OwnerMembershipDetail(TimeBasedModel):
    membership = OneToOneField("users.Membership", on_delete=CASCADE, related_name="owner_detail")
    bio = TextField(blank=True, null=True)
    website = CharField(max_length=255, blank=True, null=True)

    class Meta(auto_prefetch.Model.Meta):
        verbose_name = _("Owner Membership Detail")
        verbose_name_plural = _("Owner Membership Details")


class AdminMembershipDetail(TimeBasedModel):
    membership = OneToOneField("users.Membership", on_delete=CASCADE, related_name="admin_detail")
    department = CharField(max_length=100, blank=True, null=True)

    class Meta(auto_prefetch.Model.Meta):
        verbose_name = _("Admin Membership Detail")
        verbose_name_plural = _("Admin Membership Details")


class StaffMembershipDetail(TimeBasedModel):
    """Covers Manager / Sales / Inventory / Accountant / Teacher / Front Desk staff."""

    membership = OneToOneField("users.Membership", on_delete=CASCADE, related_name="staff_detail")
    team = CharField(max_length=100, blank=True, null=True)

    class Meta(auto_prefetch.Model.Meta):
        verbose_name = _("Staff Membership Detail")
        verbose_name_plural = _("Staff Membership Details")


class State(Model):
    """
    A state/province/region within a country, used to validate and offer
    dropdown choices for Organization.state. Seeded per-country via the
    `seed_states` management command as new markets are supported —
    not all 195 countries are expected to be present at once.
    """

    country = CharField(
        max_length=2,
        db_index=True,
        help_text="ISO 3166-1 alpha-2 country code.",
    )
    name = CharField(max_length=100)
    code = CharField(
        max_length=10,
        blank=True,
        help_text="ISO 3166-2 subdivision code, e.g. 'NG-AB'.",
    )

    class Meta:
        unique_together = ("country", "name")
        ordering = ["country", "name"]
        verbose_name = "State"
        verbose_name_plural = "States"

    def __str__(self):
        return f"{self.name} ({self.country})"
