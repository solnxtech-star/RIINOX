from allauth.account.decorators import secure_admin_login
from django.conf import settings
from django.contrib import admin
from django.contrib.auth import admin as auth_admin
from django.utils.translation import gettext_lazy as _

from .forms import UserAdminChangeForm
from .forms import UserAdminCreationForm
from .models import User, Organization, Membership, Plan, Feature, PlanFeature

if settings.DJANGO_ADMIN_FORCE_ALLAUTH:
    # Force the `admin` sign in process to go through the `django-allauth` workflow:
    # https://docs.allauth.org/en/latest/common/admin.html#admin
    admin.autodiscover()
    admin.site.login = secure_admin_login(admin.site.login)  # type: ignore[method-assign]


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
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                ),
            },
        ),
        (_("Important dates"), {"fields": ("last_login", "date_joined")}),
    )
    list_display = ["email", "name", "is_superuser"]
    search_fields = ["name"]
    ordering = ["id"]
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "password1", "password2"),
            },
        ),
    )


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ["id","name", "created_at", "updated_at"]
    search_fields = ["name"]
    ordering = ["-created_at"]


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ["id","user", "organization", "role", "is_active", "created_at"]
    list_filter = ["role", "is_active", "created_at"]
    search_fields = ["user__email", "organization__name"]
    ordering = ["-created_at"]


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = ["id","name", "price", "created_at", "updated_at"]
    search_fields = ["name"]
    ordering = ["-created_at"]


@admin.register(Feature)
class FeatureAdmin(admin.ModelAdmin):
    list_display = ["id","name", "code", "created_at", "updated_at"]
    search_fields = ["name", "code"]
    ordering = ["-created_at"]


@admin.register(PlanFeature)
class PlanFeatureAdmin(admin.ModelAdmin):
    list_display = ["id","feature", "plan", "created_at", "updated_at"]
    list_filter = ["plan"]
    search_fields = ["feature__name", "plan__name"]
    ordering = ["-created_at"]
