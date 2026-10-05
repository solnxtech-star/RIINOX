


from core.applications.users.defaults import BUSINESS_TYPES
from core.applications.users.defaults import MODULE_BY_CODE
from core.applications.users.defaults import STATES_BY_COUNTRY
from core.applications.users.models import BusinessType
from core.applications.users.models import Permission
from core.applications.users.models import State
from core.helper.enums import PermissionCode
from core.seeding.helper import tally
from core.seeding.helper import upsert
from core.seeding.registry import SeedResult
from core.seeding.registry import register


@register("permissions")
def seed_permissions() -> SeedResult:
    """
        Permission catalog, one row per PermissionCode
        (code-owned: synced, never deleted).
    """
    return tally(
        upsert(
            Permission,
            lookup={"code": code.value},
            defaults={
                "name": code.value.replace("_", " ").title(),
                "module": MODULE_BY_CODE[code],  # KeyError here = a code with no module: fail loudly
            },
        )
        for code in PermissionCode
    )


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
