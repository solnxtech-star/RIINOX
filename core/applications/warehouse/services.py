from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any

from django.db import transaction
from django.db.models import Q

from core.applications.notification.audit import services as audit
from core.applications.notification.audit.action import AuditAction
from core.applications.subscriptions.services.entitlements import (
    ensure_subscription_active,
)
from core.applications.subscriptions.services.usage import consume_usage
from core.applications.subscriptions.services.usage import release_usage
from core.applications.warehouse.errors import LocationErrorCode as Err
from core.applications.warehouse.errors import fail
from core.applications.warehouse.models import Warehouse
from core.helper.enums import LocationType
from core.helper.enums import UsageMetric

if TYPE_CHECKING:
    from core.applications.notification.audit.context import AuditContext
    from core.applications.users.models import BusinessType
    from core.applications.users.models import Organization
    from core.applications.users.models import User

__all__ = [
    "create_default_location",
    "create_warehouse",
    "deactivate_warehouse",
    "delete_warehouse",
    "reactivate_warehouse",
    "set_default_location",
]

DEFAULT_LOCATION_NAME = "Main Branch"
DEFAULT_LOCATION_CODE = "MAIN"

DEFAULT_LOCATION_BY_BUSINESS_TYPE = {
    "wholesale_distribution": LocationType.WAREHOUSE,
    "retail": LocationType.STORE,
    "supermarket": LocationType.STORE,
    "pharmacy": LocationType.STORE,
    "services": LocationType.OFFICE,
}


def _lock(warehouse: Warehouse) -> Warehouse:
    """Re-read the row under a row lock so checks below can't race a concurrent change."""
    return Warehouse.objects.select_for_update().get(pk=warehouse.pk)


def _free_code(organization_id, base: str) -> str:
    """`base`, or `base-2`, `base-3`, ... whichever is not yet used in this organization."""
    taken = set(
        Warehouse.objects.filter(organization_id=organization_id, code__istartswith=base)
        .values_list("code", flat=True)
    )
    taken = {code.upper() for code in taken}
    if base.upper() not in taken:
        return base
    suffix = 2
    while f"{base}-{suffix}".upper() in taken:
        suffix += 1
    return f"{base}-{suffix}"


@transaction.atomic
def create_warehouse(*, organization: Organization, name: str, code: str, **kwargs: Any) -> Warehouse:
    """
    Creates a new physical warehouse/location for the organization.
    Enforces active subscription standing and the max_locations plan limit server-side.
    Only ACTIVE locations count, so organizations can retire locations without losing history.
    """
    ensure_subscription_active(organization)  # quick early refusal if the plan has lapsed

    warehouse = Warehouse.objects.create(organization=organization, name=name, code=code, **kwargs)

    # Last step: reserve one location. At the limit this raises PLAN_LIMIT_REACHED and,
    # being inside atomic(), also undoes the insert above.
    consume_usage(organization, UsageMetric.LOCATIONS)
    return warehouse


@transaction.atomic
def deactivate_warehouse(warehouse: Warehouse) -> Warehouse:
    """
    Retire a location: history stays, its slot is given back. Not gated by the plan:
    shrinking is always allowed. The default location can't be retired.
    """
    locked = _lock(warehouse)
    if locked.is_default:
        raise fail(Err.DEFAULT_LOCATION_PROTECTED)
    if locked.is_active:
        Warehouse.objects.filter(pk=locked.pk).update(is_active=False)
        release_usage(locked.organization_id, UsageMetric.LOCATIONS)
    warehouse.is_active = False
    return warehouse


@transaction.atomic
def reactivate_warehouse(warehouse: Warehouse) -> Warehouse:
    """Bring a retired location back. Needs a free slot, so it can be refused at the plan limit."""
    ensure_subscription_active(warehouse.organization)
    if Warehouse.objects.filter(pk=warehouse.pk, is_active=False).update(is_active=True):
        consume_usage(warehouse.organization, UsageMetric.LOCATIONS)
    warehouse.is_active = True
    return warehouse


@transaction.atomic
def delete_warehouse(*, warehouse: Warehouse, user: User | None = None) -> None:
    """
    Hard-deletes a location, releasing its quota slot if active.
    The default location can't be deleted.
    """
    locked = _lock(warehouse)
    if locked.is_default:
        raise fail(Err.DEFAULT_LOCATION_PROTECTED)
    locked.delete()
    if locked.is_active:
        release_usage(locked.organization_id, UsageMetric.LOCATIONS)


@transaction.atomic
def create_default_location(
    *,
    organization: Organization,
    business_type: BusinessType | None = None,
    actor: User | None = None,
    audit_context: AuditContext | None = None,
) -> Warehouse:
    """
    Create the organization's default ("Main Branch") location, counted against the
    plan's location limit like any other active location.

    Idempotent and safe under concurrency: the organization row is locked, so two
    callers can't both create a default; the second gets the first one's location.
    """
    # Lock via the instance's own class: this module must not import users at runtime
    # (users.services imports it, so that would be a circular import).
    type(organization).objects.select_for_update().only("pk").get(pk=organization.pk)
    existing = Warehouse.objects.filter(organization=organization, is_default=True).first()
    if existing:
        return existing

    location_type = DEFAULT_LOCATION_BY_BUSINESS_TYPE.get(
        getattr(business_type, "code", None), LocationType.BRANCH
    )
    location = Warehouse.objects.create(
        organization=organization,
        name=DEFAULT_LOCATION_NAME,
        code=_free_code(organization.pk, DEFAULT_LOCATION_CODE),
        location_type=location_type,
        address=organization.address,
        is_default=True,
    )
    consume_usage(organization, UsageMetric.LOCATIONS)
    audit.record(
        action=AuditAction.LOCATION_CREATED,
        organization=organization,
        actor=actor,
        resource=location,
        new_values={
            "name": location.name,
            "code": location.code,
            "location_type": location_type,
            "is_default": True,
        },
        context=audit_context,
    )
    return location


@transaction.atomic
def set_default_location(
    *,
    location: Warehouse,
    actor: User | None = None,
    audit_context: AuditContext | None = None,
) -> Warehouse:
    """
    Make `location` the organization's default, unsetting the previous one.
    The current default and the target are locked (in pk order, to avoid deadlocks)
    so concurrent switches serialize instead of tripping the one-default constraint.
    """
    # Normalize: a freshly created instance holds a hex string pk, DB rows hold a UUID.
    target_pk = Warehouse._meta.pk.to_python(location.pk)
    rows = list(
        Warehouse.objects.select_for_update()
        .filter(organization_id=location.organization_id)
        .filter(Q(is_default=True) | Q(pk=target_pk))
        .order_by("pk")
    )
    target = next((row for row in rows if row.pk == target_pk), None)
    if target is None:
        raise Warehouse.DoesNotExist(f"Location {location.pk} no longer exists.")
    previous = next((row for row in rows if row.is_default and row.pk != target_pk), None)

    if target.is_default:
        return target
    if not target.is_active:
        raise fail(Err.LOCATION_INACTIVE)

    if previous is not None:
        Warehouse.objects.filter(pk=previous.pk).update(is_default=False)
    Warehouse.objects.filter(pk=target.pk).update(is_default=True)
    target.is_default = True
    location.is_default = True

    audit.record(
        action=AuditAction.LOCATION_DEFAULT_CHANGED,
        organization=target.organization,
        actor=actor,
        resource=target,
        previous_values={"default_location": str(previous.pk) if previous else None},
        new_values={"default_location": str(target.pk)},
        context=audit_context,
    )
    return target
