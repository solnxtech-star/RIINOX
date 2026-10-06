from allauth.account.decorators import secure_admin_login
from django.conf import settings
from django.contrib import admin
from django.contrib.auth import admin as auth_admin
from django.core.exceptions import ValidationError
from django.db.models import Count
from django.db.models import Prefetch
from django.forms.models import BaseInlineFormSet
from django.utils.translation import gettext_lazy as _

from core.applications.subscriptions.models import Feature
from core.applications.subscriptions.models import Plan
from core.applications.subscriptions.models import PlanFeature
from core.applications.subscriptions.models import Subscription
from core.applications.users import services
from core.applications.users.models import AdminMembershipDetail
from core.applications.users.models import BusinessType
from core.applications.users.models import Invitation
from core.applications.users.models import Membership
from core.applications.users.models import Organization
from core.applications.users.models import OrganizationBusinessType
from core.applications.users.models import OwnerMembershipDetail
from core.applications.users.models import Permission
from core.applications.users.models import Role
from core.applications.users.models import RolePermission
from core.applications.users.models import StaffMembershipDetail
from core.applications.users.models import State
from core.applications.users.models import User

from .forms import UserAdminChangeForm
from .forms import UserAdminCreationForm

if settings.DJANGO_ADMIN_FORCE_ALLAUTH:
    # Force the `admin` sign in process to go through the `django-allauth` workflow:
    # https://docs.allauth.org/en/latest/common/admin.html#admin
    admin.autodiscover()
    admin.site.login = secure_admin_login(admin.site.login)  # type: ignore[method-assign]


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------
class UserMembershipInline(admin.TabularInline):
    """Which organizations this user belongs to, and as what."""

    model = Membership
    fk_name = "user"
    extra = 0
    fields = ["organization", "role", "is_active"]
    raw_id_fields = ["organization", "role"]
    show_change_link = True


@admin.register(User)
class UserAdmin(auth_admin.UserAdmin):
    form = UserAdminChangeForm
    add_form = UserAdminCreationForm
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        (_("Personal info"), {"fields": ("name",)}),
        (
            _("Permissions"),
            {
                "fields": (
                    "is_active",
                    "is_verified",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                ),
            },
        ),
        (_("Important dates"), {"fields": ("last_login", "date_joined")}),
    )
    list_display = [
        "id", "email", "name", "is_active",
        "is_verified", "is_staff", "is_superuser", "date_joined"
    ]
    list_filter = ["is_active", "is_verified", "is_staff", "is_superuser"]
    search_fields = ["email", "name"]
    ordering = ["id"]
    inlines = [UserMembershipInline]
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "password1", "password2"),
            },
        ),
    )


# ---------------------------------------------------------------------------
# Business type catalog
# ---------------------------------------------------------------------------
@admin.register(BusinessType)
class BusinessTypeAdmin(admin.ModelAdmin):
    list_display = [
        "id", "name",
        "code", "show_in_onboarding",
        "is_active", "sort_order",
        "organization_count"
    ]
    list_editable = ["show_in_onboarding", "is_active", "sort_order"]
    list_filter = ["show_in_onboarding", "is_active"]
    search_fields = ["name", "code"]  # also powers autocomplete in the organization inline
    ordering = ["sort_order", "name"]

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_organization_count=Count("organization_links"))

    @admin.display(description=_("Organizations"), ordering="_organization_count")
    def organization_count(self, obj):
        return obj._organization_count

    def get_readonly_fields(self, request, obj=None):
        # `code` is the key the API and frontend use; renaming it breaks clients.
        return ["code", "created_at", "updated_at"] if obj else ["created_at", "updated_at"]


# ---------------------------------------------------------------------------
# Organizations
# ---------------------------------------------------------------------------
class OrganizationBusinessTypeFormSet(BaseInlineFormSet):
    """Enforce the invariants a DB constraint can't: >= 1 type, exactly 1 primary."""

    def selected(self) -> list[dict]:
        return [f.cleaned_data for f in self.forms if f.cleaned_data and not f.cleaned_data.get("DELETE")]

    def clean(self):
        super().clean()
        if any(self.errors):
            return
        selected = self.selected()
        if not selected:
            raise ValidationError(_("An organization needs at least one business type."))
        if sum(1 for row in selected if row.get("is_primary")) != 1:
            raise ValidationError(_("Exactly one business type must be marked as primary."))


class OrganizationBusinessTypeInline(admin.TabularInline):
    model = OrganizationBusinessType
    formset = OrganizationBusinessTypeFormSet
    extra = 0
    fields = ["business_type", "is_primary"]
    autocomplete_fields = ["business_type"]


class OrganizationSubscriptionInline(admin.StackedInline):
    model = Subscription
    extra = 0
    max_num = 1
    can_delete = False


class OrganizationMembershipInline(admin.TabularInline):
    model = Membership
    fk_name = "organization"
    extra = 0
    fields = ["user", "invited_email", "role", "is_active"]
    raw_id_fields = ["user", "role"]
    show_change_link = True


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = [
        "id", "name",
        "primary_type", "plan",
        "state", "staff_size",
        "is_active", "created_by",
        "created_at"
    ]
    list_filter = ["is_active", "business_types", "state", "staff_size", "created_at"]
    search_fields = ["name", "legal_name", "registration_number", "email", "phone"]
    ordering = ["-created_at"]
    date_hierarchy = "created_at"
    readonly_fields = ["created_by", "created_at", "updated_at"]
    inlines = [OrganizationBusinessTypeInline, OrganizationSubscriptionInline, OrganizationMembershipInline]
    fieldsets = (
        (_("Identity"), {"fields": ("name", "legal_name", "registration_number", "tax_id", "staff_size")}),
        (_("Contact"), {"fields": ("email", "phone", "address", "state")}),
        (_("Locale"), {"fields": ("country", "currency", "timezone")}),
        (
            _("Branding & documents"),
            {
                "fields": (
                    "logo",
                    "header_text",
                    "footer_text",
                    "domain",
                    "invoice_template",
                    "receipt_template",
                    "quote_template",
                    "credit_note_template",
                ),
                "classes": ("collapse",),
            },
        ),
        (_("Status"), {"fields": ("is_active", "created_by", "created_at", "updated_at")}),
    )

    def get_queryset(self, request):
        links = OrganizationBusinessType.objects.select_related("business_type")
        return (
            super()
            .get_queryset(request)
            .select_related("created_by", "subscription__plan")
            .prefetch_related(Prefetch("organization_business_types", queryset=links))
        )

    @admin.display(description=_("Primary type"))
    def primary_type(self, obj):
        return obj.primary_business_type or "—"

    @admin.display(description=_("Plan"))
    def plan(self, obj):
        return obj.current_plan or "—"

    # Organizations are created by the onboarding endpoint (services.create_organization),
    # which also creates roles, subscription, Owner membership and numbering. A row
    # added here would be an unusable, half-built tenant.
    def has_add_permission(self, request):
        return False

    # Deleting cascades through every tenant record (PRD §44); deactivate instead.
    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser

    def save_formset(self, request, form, formset, change):
        if isinstance(formset, OrganizationBusinessTypeFormSet):
            # Single write path: same service (and same primary-flag ordering) as the API.
            rows = formset.selected()
            primary = next(row["business_type"] for row in rows if row["is_primary"])
            services.set_business_types(
                organization=form.instance,
                business_types=[row["business_type"] for row in rows],
                primary=primary,
            )
            return
        super().save_formset(request, form, formset, change)


# ---------------------------------------------------------------------------
# Permissions & roles
# ---------------------------------------------------------------------------
@admin.register(Permission)
class PermissionAdmin(admin.ModelAdmin):
    list_display = ["id", "code", "name", "module"]
    list_filter = ["module"]
    search_fields = ["code", "name"]  # powers autocomplete in the role inline
    ordering = ["module", "name"]


class RolePermissionInline(admin.TabularInline):
    model = RolePermission
    extra = 0
    autocomplete_fields = ["permission"]


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = [
        "id", "name", "organization",
        "slug", "is_system", "permission_count"
    ]
    list_filter = ["is_system"]
    search_fields = ["name", "slug", "organization__name"]
    raw_id_fields = ["organization"]
    ordering = ["organization", "name"]
    inlines = [RolePermissionInline]

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("organization")
            .annotate(_permission_count=Count("role_permissions"))
        )

    @admin.display(description=_("Permissions"), ordering="_permission_count")
    def permission_count(self, obj):
        return obj._permission_count

    def get_readonly_fields(self, request, obj=None):
        # is_system is never set by hand; a role never moves between tenants;
        # system roles keep their name/slug (their permission set stays editable).
        readonly = ["is_system"]
        if obj:
            readonly.append("organization")
            if obj.is_system:
                readonly += ["name", "slug"]
        return readonly

    def has_delete_permission(self, request, obj=None):
        return not (obj and obj.is_system)


# ---------------------------------------------------------------------------
# Memberships
# ---------------------------------------------------------------------------
class OwnerDetailInline(admin.StackedInline):
    model = OwnerMembershipDetail
    extra = 0
    max_num = 1


class AdminDetailInline(admin.StackedInline):
    model = AdminMembershipDetail
    extra = 0
    max_num = 1


class StaffDetailInline(admin.StackedInline):
    model = StaffMembershipDetail
    extra = 0
    max_num = 1

@admin.register(Invitation)
class InvitationAdmin(admin.ModelAdmin):
    list_display = (
        "id", "email", "name", "organization", "role",
        "status_display", "expires_at", "invited_by", "created_at",
    )
    list_filter = ("status", "organization")
    search_fields = ("email", "name", "organization__name")
    list_select_related = ("organization", "role", "invited_by")
    raw_id_fields = ("organization", "role", "invited_by", "accepted_by")
    readonly_fields = (
        "token_hash", "status", "expires_at", "last_sent_at",
        "invited_by", "accepted_by", "accepted_at", "revoked_at",
    )

    @admin.display(description="Status")
    def status_display(self, obj):
        return obj.display_status  # includes the derived "expired"

    # Invitations are created through the service so the token is generated,
    # hashed and emailed. A row made here would have no usable link.
    def has_add_permission(self, request):
        return False

    # Records are never hard-deleted (PRD §44); revoke instead.
    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ["id", "user", "organization", "role", "is_active", "created_at"]
    # `role__slug`, not `role`: roles are per-organization, so a plain role filter would
    # list every role of every tenant.
    list_filter = ["role__slug", "is_active", "created_at"]
    search_fields = ["user__email", "user__name", "organization__name"]
    raw_id_fields = ["user", "organization", "role"]
    list_select_related = ["user", "organization", "role"]
    readonly_fields = ["invitation", "created_at", "updated_at"]
    ordering = ["-created_at"]
    inlines = [OwnerDetailInline, AdminDetailInline, StaffDetailInline]


# ---------------------------------------------------------------------------
# Plans, features, subscriptions
# ---------------------------------------------------------------------------
class PlanFeatureInline(admin.TabularInline):
    model = PlanFeature
    extra = 0
    autocomplete_fields = ["feature"]


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "name",
        "price",
        "billing_period_days",
        "trial_period_days",
        "max_users",
        "max_products",
        "max_locations",
        "is_active",
    ]
    list_filter = ["is_active"]
    search_fields = ["name"]
    ordering = ["price"]
    inlines = [PlanFeatureInline]


@admin.register(Feature)
class FeatureAdmin(admin.ModelAdmin):
    list_display = ["id", "name", "code", "created_at", "updated_at"]
    search_fields = ["name", "code"]  # powers autocomplete in the plan inline
    ordering = ["name"]


@admin.register(PlanFeature)
class PlanFeatureAdmin(admin.ModelAdmin):
    list_display = ["feature", "plan", "enabled", "created_at"]
    list_filter = ["plan", "enabled"]
    list_select_related = ["feature", "plan"]
    search_fields = ["feature__name", "plan__name"]
    ordering = ["plan", "feature"]


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = [
        "id", "organization",
        "plan", "status", "trial_ends_at",
        "current_period_end", "canceled_at"
    ]
    list_filter = ["status", "plan"]
    list_select_related = ["organization", "plan"]
    search_fields = ["organization__name"]
    raw_id_fields = ["organization"]
    ordering = ["-created_at"]

@admin.register(State)
class StateAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "country", "code")
    list_filter = ("country",)
    search_fields = ("name",)
