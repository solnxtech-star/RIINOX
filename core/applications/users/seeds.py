


from core.applications.users.defaults import ALL
from core.applications.users.defaults import BUSINESS_TYPES
from core.applications.users.defaults import DEFAULT_ROLE_PERMISSIONS
from core.applications.users.defaults import MODULE_BY_CODE
from core.applications.users.defaults import STATES_BY_COUNTRY
from core.applications.users.models import BusinessType
from core.applications.users.models import Permission
from core.applications.users.models import Role
from core.applications.users.models import RolePermission
from core.applications.users.models import State
from core.helper.enums import PermissionCode
from core.seeding.helper import CREATED
from core.seeding.helper import tally
from core.seeding.helper import upsert
from core.seeding.registry import SeedResult
from core.seeding.registry import register


def _grant_to_existing_roles(code: str) -> None:
    """
    A permission just added to the catalog goes to the matching system roles of
    organizations that already exist. It runs only on creation, so a permission
    an organization later removes from a role stays removed.
    """
    slugs = [
        slug
        for slug, grant in DEFAULT_ROLE_PERMISSIONS.items()
        if grant == ALL or code in {str(c) for c in grant}
    ]
    permission = Permission.objects.get(code=code)
    RolePermission.objects.bulk_create(
        [
            RolePermission(role=role, permission=permission)
            for role in Role.objects.filter(is_system=True, slug__in=slugs)
        ],
        ignore_conflicts=True,
    )


@register("permissions")
def seed_permissions() -> SeedResult:
    """Permission catalog, one row per PermissionCode (code-owned: synced, never deleted)."""
    statuses = []
    for code in PermissionCode:
        status = upsert(
            Permission,
            lookup={"code": code.value},
            defaults={
                "name": code.value.replace("_", " ").title(),
                "module": MODULE_BY_CODE[code],
            },
        )
        if status == CREATED:
            _grant_to_existing_roles(code.value)  # no-op on a fresh database: no roles yet
        statuses.append(status)
    return tally(statuses)

@register("business_types")
def seed_business_types() -> SeedResult:
    """Business type catalog from the PRD (code-owned: synced)."""
    return tally(
        upsert(
            BusinessType,
            lookup={"code": entry["code"]},
            defaults={key: value for key, value in entry.items() if key != "code"},
        )
        for entry in BUSINESS_TYPES
    )


@register("states")
def seed_states() -> SeedResult:
    """States/regions backing the "Select state" dropdown (code-owned: synced)."""
    return tally(
        upsert(State, lookup={"country": country, "name": name}, defaults={})
        for country, names in STATES_BY_COUNTRY.items()
        for name in names
    )
