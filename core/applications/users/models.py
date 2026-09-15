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
from django.db.models import DecimalField
from django.db.models import EmailField
from django.db.models import FileField
from django.db.models import ImageField
from django.db.models import Index
from django.db.models import ManyToManyField
from django.db.models import OneToOneField
from django.db.models import PositiveIntegerField
from django.db.models import Q
from django.db.models import TextChoices
from django.db.models import TextField
from django.db.models import UniqueConstraint
from django.db.models import UUIDField
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from core.helper.enums import TEMPLATE_TYPES
from core.helper.enums import OrganizationTypeChoices
from core.helper.enums import SubscriptionStatus
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



class Plan(TimeBasedModel):
    """
    A billable plan tier. Numeric limits live directly on the plan since
    the PRD requires them to be enforced server-side (max users, products,
    locations, etc.); boolean capability toggles (API access, custom
    templates, ...) are handled separately via Feature/PlanFeature.
    """

    name = CharField(max_length=50, unique=True)
    description = TextField(blank=True, null=True)
    price = DecimalField(max_digits=10, decimal_places=2, default=0)
    billing_period_days = PositiveIntegerField(
        default=30,
        help_text=_("Length of one billing cycle, in days."),
    )
    trial_period_days = PositiveIntegerField(default=0)

    # Server-enforced limits (0 or null = unlimited, per project convention).
    max_users = PositiveIntegerField(null=True, blank=True)
    max_products = PositiveIntegerField(null=True, blank=True)
    max_locations = PositiveIntegerField(null=True, blank=True)
    max_transactions_per_month = PositiveIntegerField(null=True, blank=True)
    storage_limit_mb = PositiveIntegerField(null=True, blank=True)

    is_active = BooleanField(default=True)

    class Meta(auto_prefetch.Model.Meta):
        verbose_name = _("Plan")
        verbose_name_plural = _("Plans")
        ordering = ["price"]

    def __str__(self):
        return self.name


class Feature(TimeBasedModel):
    """
    A togglable capability, e.g. 'API_ACCESS', 'CUSTOM_TEMPLATES',
    'UNLIMITED_INVOICES', 'PRIORITY_SUPPORT'.
    """

    code = CharField(max_length=50, unique=True)
    name = CharField(max_length=100)
    description = TextField(blank=True, null=True)

    class Meta(auto_prefetch.Model.Meta):
        verbose_name = _("Feature")
        verbose_name_plural = _("Features")
        ordering = ["name"]

    def __str__(self):
        return self.name


class PlanFeature(TimeBasedModel):
    plan = auto_prefetch.ForeignKey(
        "users.Plan", on_delete=CASCADE,
        related_name="plan_features"
    )
    feature = auto_prefetch.ForeignKey(
        "users.Feature", on_delete=CASCADE,
        related_name="feature_plans"
    )
    enabled = BooleanField(default=True)

    class Meta(auto_prefetch.Model.Meta):
        verbose_name = _("Plan Feature")
        verbose_name_plural = _("Plan Features")
        constraints: ClassVar = [
            UniqueConstraint(fields=["plan", "feature"], name="unique_plan_feature"),
        ]

    def __str__(self):
        return f"{self.feature.name} on {self.plan.name}"


class Subscription(TimeBasedModel):
    """
    The organization's actual subscription lifecycle — separate from Plan
    (a catalog item) so trial/renewal/cancellation state has somewhere to
    live, per the PRD's subscription-based SaaS access requirement.
    """

    organization = OneToOneField(
        "users.Organization",
        on_delete=CASCADE,
        related_name="subscription",
    )
    plan = auto_prefetch.ForeignKey(
        "users.Plan", on_delete=PROTECT, related_name="subscriptions"
    )
    status = CharField(
        max_length=20, choices=SubscriptionStatus.choices,
        default=SubscriptionStatus.TRIALING
    )
    trial_ends_at = DateTimeField(null=True, blank=True)
    current_period_start = DateTimeField(null=True, blank=True)
    current_period_end = DateTimeField(null=True, blank=True)
    canceled_at = DateTimeField(null=True, blank=True)

    class Meta(auto_prefetch.Model.Meta):
        verbose_name = _("Subscription")
        verbose_name_plural = _("Subscriptions")

    def __str__(self):
        return f"{self.organization.name} — {self.plan.name} ({self.status})"



class Organization(TimeBasedModel):
    """
    A tenant. Every organization-scoped record in the system must resolve
    back to one of these; this model itself is the root of that boundary.
    """

    # Identity
    name = CharField(max_length=255, help_text=_("Trading name."))
    legal_name = CharField(max_length=255, blank=True)
    type = CharField(
        _("Business Type"),
        max_length=50,
        choices=OrganizationTypeChoices.choices,
        default=OrganizationTypeChoices.BUSINESS,
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
