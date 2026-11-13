from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING
from typing import Any

from rest_framework.exceptions import ValidationError

from core.applications.warehouse.errors import LocationErrorCode as Err
from core.applications.warehouse.errors import fail

if TYPE_CHECKING:
    from django.db.models import QuerySet

# System role slugs that always see every location. Duplicated from
# users.defaults on purpose (no import of the users app at load time); the
# tests assert the two stay equal.
ALL_LOCATION_ROLE_SLUGS = frozenset({"owner", "administrator"})

LOCATION_QUERY_PARAM = "location"


def _to_uuid(value: Any) -> uuid.UUID | None:
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(getattr(value, "pk", value)))
    except (TypeError, ValueError, AttributeError):
        return None


@dataclass(frozen=True, slots=True)
class LocationScope:
    """Which locations a member may see and act in."""

    all_locations: bool
    location_ids: frozenset[uuid.UUID] = frozenset()

    # ------------------------------------------------------------------ #
    # Construction
    # ------------------------------------------------------------------ #
    @classmethod
    def everything(cls) -> LocationScope:
        return cls(all_locations=True)

    @classmethod
    def nothing(cls) -> LocationScope:
        return cls(all_locations=False)

    @classmethod
    def for_membership(cls, membership: Any) -> LocationScope:
        """The scope of an (active) membership. `None` means no access at all."""
        if membership is None:
            return cls.nothing()
        role = getattr(membership, "role", None)
        if role is not None and role.is_system and role.slug in ALL_LOCATION_ROLE_SLUGS:
            return cls.everything()
        if membership.all_locations:
            return cls.everything()
        ids = membership.membership_locations.values_list("location_id", flat=True)
        return cls(all_locations=False, location_ids=frozenset(_to_uuid(pk) for pk in ids))

    @classmethod
    def for_user(cls, user: Any, organization: Any) -> LocationScope:
        """For callers without a request (services, jobs). Superusers see everything."""
        if getattr(user, "is_superuser", False):
            return cls.everything()
        if not getattr(user, "is_authenticated", False) or organization is None:
            return cls.nothing()
        from core.applications.users.models import Membership  # users imports this module

        membership = (
            Membership.objects.effective()
            .for_user(user)
            .filter(organization_id=getattr(organization, "pk", organization))
            .select_related("role")
            .first()
        )
        return cls.for_membership(membership)

    # ------------------------------------------------------------------ #
    # Questions
    # ------------------------------------------------------------------ #
    def allows(self, location: Any) -> bool:
        """`location` is a Warehouse, its id, or None (a record with no location)."""
        if self.all_locations:
            return True
        location_id = _to_uuid(location)
        return location_id is not None and location_id in self.location_ids

    def require(self, location: Any) -> None:
        """Raise LOCATION_ACCESS_DENIED (403) unless the member may act in `location`."""
        if not self.allows(location):
            raise fail(Err.LOCATION_ACCESS_DENIED)

    def require_all_locations(self) -> None:
        """For organization-wide actions (creating a location, changing the default)."""
        if not self.all_locations:
            raise fail(Err.ALL_LOCATIONS_REQUIRED)

    # ------------------------------------------------------------------ #
    # Querysets
    # ------------------------------------------------------------------ #
    def apply(self, queryset: QuerySet, field: str = "warehouse") -> QuerySet:
        """
        Limit `queryset` to rows whose `field` (a path to a Warehouse, e.g.
        "warehouse", "sale__warehouse", or "pk" on Warehouse itself) is in scope.
        Rows where `field` is NULL drop out for limited members.
        """
        if self.all_locations:
            return queryset
        if not self.location_ids:
            return queryset.none()
        lookup = "pk__in" if field == "pk" else f"{field}__in"
        return queryset.filter(**{lookup: self.location_ids})

    def narrow(self, queryset: QuerySet, location: Any, field: str = "warehouse") -> QuerySet:
        """
        Apply an explicit location filter (the `?location=` query parameter) on top
        of `apply`. A location outside the scope yields an empty result, never an
        error, so the API doesn't reveal which locations exist.
        """
        if location is None:
            return queryset
        location_id = _to_uuid(location)
        if location_id is None or not self.allows(location_id):
            return queryset.none()
        lookup = "pk" if field == "pk" else field
        return queryset.filter(**{lookup: location_id})


def parse_location_param(query_params: Any) -> uuid.UUID | None:
    """The `?location=<uuid>` filter, or None. A malformed id is a 400."""
    raw = query_params.get(LOCATION_QUERY_PARAM)
    if raw in (None, ""):
        return None
    location_id = _to_uuid(raw.strip())
    if location_id is None:
        raise ValidationError({LOCATION_QUERY_PARAM: ["Must be a valid location id."]})
    return location_id
