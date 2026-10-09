from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.db import transaction

from core.applications.subscriptions.services import (
    check_plan_limit,
    ensure_subscription_active,
)
from core.applications.warehouse.models import Warehouse

if TYPE_CHECKING:
    from core.applications.users.models import Organization

__all__ = [
    "create_warehouse",
    "ensure_location_limit",
]


def ensure_location_limit(organization: Organization) -> None:
    """
    Asserts that adding a new warehouse/location does not exceed the organization's plan limit.
    Deactivated locations are excluded so organizations can retire locations without losing history.
    """
    active_count = Warehouse.objects.filter(
        organization=organization,
        is_active=True,
    ).count()
    check_plan_limit(organization, "max_locations", active_count)


@transaction.atomic
def create_warehouse(
    *,
    organization: Organization,
    name: str,
    code: str,
    **kwargs: Any,
) -> Warehouse:
    """
    Creates a new physical warehouse/location for the organization.
    Enforces active subscription standing and the max_locations plan limit server-side.
    """
    ensure_subscription_active(organization)
    ensure_location_limit(organization)

    return Warehouse.objects.create(
        organization=organization,
        name=name,
        code=code,
        **kwargs,
    )
