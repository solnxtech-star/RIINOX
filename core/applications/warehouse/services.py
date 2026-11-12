from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any

from django.db import transaction

from core.applications.notification import audit
from core.applications.notification.audit.action import AuditAction
from core.applications.subscriptions.services.entitlements import (
    ensure_subscription_active,
)
from core.applications.subscriptions.services.usage import consume_usage
from core.applications.subscriptions.services.usage import release_usage
from core.applications.users.models import User
from core.applications.warehouse.models import Warehouse
from core.helper.enums import LocationType
from core.helper.enums import UsageMetric

if TYPE_CHECKING:
    from core.applications.users.models import Organization

__all__ = ["create_warehouse", "deactivate_warehouse", "reactivate_warehouse", "delete_warehouse"]



DEFAULT_LOCATION_BY_BUSINESS_TYPE = {
    "wholesale_distribution": LocationType.WAREHOUSE,
    "retail": LocationType.STORE,
    "supermarket": LocationType.STORE,
    "pharmacy": LocationType.STORE,
    "services": LocationType.OFFICE,
}


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
    """Retire a location: history stays, its slot is given back. Not gated: shrinking is always allowed."""
    if Warehouse.objects.filter(pk=warehouse.pk, is_active=True).update(is_active=False):
        release_usage(warehouse.organization, UsageMetric.LOCATIONS)
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
    """Hard-deletes a warehouse location, releasing its quota slot if active."""
    was_active = warehouse.is_active
    warehouse.delete()
    if was_active:
        release_usage(warehouse.organization, UsageMetric.LOCATIONS)


@transaction.atomic
def create_default_location(*, organization, business_type=None, audit_context=None, actor=None) -> Warehouse:
    """Idempotent: returns the existing default if there is one."""
    existing = Warehouse.objects.filter(organization=organization, is_default=True).first()
    if existing:
        return existing

    location_type = DEFAULT_LOCATION_BY_BUSINESS_TYPE.get(
        getattr(business_type, "code", None), LocationType.BRANCH
    )
    location = Warehouse.objects.create(
        organization=organization,
        name="Main Branch",
        code="MAIN",
        location_type=location_type,
        address=organization.address,
        is_default=True,
    )
    audit.services.record(
        action=AuditAction.LOCATION_CREATED,
        organization=organization,
        actor=actor,
        resource=location,
        new_values={"name": location.name, "type": location_type, "is_default": True},
        context=audit_context,
    )
    return location
