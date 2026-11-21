"""
The built-in "Walk-in Customer": one per organization, for anonymous counter
sales (most supermarket and shop buyers are never registered).

Rules, enforced where they matter:
* Created automatically: with every new organization, by the `walk_in_customers`
  seed after each `migrate` (organizations that predate it), and on first use.
* Can never buy on credit: a sale to it must be paid in full (sales service), and
  it can't be put on a standalone invoice (invoice service).
* Can't be deactivated, deleted or given credit (customer service). It may be
  renamed (e.g. "Cash Customer").
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from django.db import IntegrityError, transaction
from django.db.models import Exists, OuterRef
from rest_framework import status

from core.applications.customers.models import Customer
from core.helper.custom_exceptions import BusinessRuleError
from core.helper.enums import CustomerStatus

if TYPE_CHECKING:
    from core.applications.users.models import Organization

logger = logging.getLogger(__name__)

WALK_IN_NAME = "Walk-in Customer"

__all__ = [
    "WALK_IN_NAME",
    "ensure_walk_in_customers",
    "get_walk_in_customer",
    "is_walk_in",
    "walk_in_protected",
]


def is_walk_in(customer: Any) -> bool:
    return bool(getattr(customer, "is_walk_in", False))


def walk_in_protected(message: str) -> BusinessRuleError:
    return BusinessRuleError("WALK_IN_CUSTOMER_PROTECTED", message, status_code=status.HTTP_409_CONFLICT)


def get_walk_in_customer(organization: Organization) -> Customer:
    """The organization's walk-in customer, created if missing. Safe under concurrency."""
    customer = Customer.objects.filter(organization=organization, is_walk_in=True).first()
    if customer is not None:
        return customer
    try:
        with transaction.atomic():
            return Customer.objects.create(
                organization=organization,
                name=WALK_IN_NAME,
                is_walk_in=True,
                status=CustomerStatus.ACTIVE,
                notes="Created automatically for counter sales to unregistered buyers.",
            )
    except IntegrityError:
        # Another request created it at the same moment (one-per-organization constraint).
        return Customer.objects.get(organization=organization, is_walk_in=True)


def ensure_walk_in_customers(*, organization_ids: list[Any] | None = None) -> int:
    """Give every organization without one its walk-in customer. Returns how many were created."""
    from core.applications.users.models import Organization

    missing = Organization.objects.annotate(
        has_walk_in=Exists(Customer.objects.filter(organization=OuterRef("pk"), is_walk_in=True))
    ).filter(has_walk_in=False)
    if organization_ids is not None:
        missing = missing.filter(pk__in=organization_ids)

    created = 0
    for organization in missing.iterator():
        try:
            get_walk_in_customer(organization)
            created += 1
        except Exception:  # one bad organization must not stop the rest
            logger.exception("walk_in_customer.failed", extra={"organization_id": str(organization.pk)})
    return created
