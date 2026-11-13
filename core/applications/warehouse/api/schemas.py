from __future__ import annotations

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter
from drf_spectacular.utils import extend_schema
from drf_spectacular.utils import extend_schema_view

ORGANIZATION_PATH_PARAM = OpenApiParameter(
    name="organization_id",
    type=OpenApiTypes.UUID,
    location=OpenApiParameter.PATH,
    description="UUID of the tenant organization owning the resource.",
)

# --------------------------------------------------------------------------- #
# Shared by every app whose records belong to a location (PRD §35, §47).
# Import these instead of re-describing location access per endpoint.
# --------------------------------------------------------------------------- #
LOCATION_FILTER_PARAM = OpenApiParameter(
    name="location",
    type=OpenApiTypes.UUID,
    location=OpenApiParameter.QUERY,
    required=False,
    description=(
        "Only return records of this location (a warehouse id from "
        "`GET /organizations/{organization_id}/warehouses/`). A location the caller "
        "can't access returns an empty list, not an error. Malformed id: 400."
    ),
)

LOCATION_ACCESS_NOTE = """
**Location access (PRD §35).** A member works either in *all* locations or in a
chosen list (`all_locations` / `locations` on the membership, see
`GET /api/memberships/` and `PUT /api/memberships/{id}/locations/`).
Owners and Administrators always have all locations.

What this means for the client:

* **Lists and details only contain the caller's locations.** A record at another
  location answers 404, exactly like a record of another organization.
  Records that have no location are only visible to members with all locations.
* **Filter by location with `?location=<warehouse id>`.** Use it for a
  "current branch" switcher. Build the switcher from
  `GET /organizations/{organization_id}/warehouses/`, which already lists only the
  locations the caller can access (default location first).
* **Writes must name a location the caller can access.** Otherwise the API answers
  `403 LOCATION_ACCESS_DENIED`. Members limited to specific locations must
  always send a location where it is optional (`400 LOCATION_REQUIRED`).
* Members with all locations see no difference: nothing is filtered unless they
  send `?location=`.
""".strip()

_WAREHOUSE_LIST_DESCRIPTION = (
    "The organization's locations (branches, stores, warehouses, offices) that the "
    "caller can access. Use it to build location pickers and the branch switcher.\n\n"
    "* `is_default` marks the organization's main location; there is always exactly one. "
    "Pre-select it when the caller can access it.\n"
    "* `location_type` is the kind of place (`branch`, `store`, `warehouse`, `office`, `other`).\n"
    "* Inactive locations are included; hide them from pickers for new records.\n\n"
    + LOCATION_ACCESS_NOTE
)

_ORG_WIDE_NOTE = (
    "\n\nOrganization-wide action: requires `MANAGE_LOCATIONS` **and** access to all "
    "locations (`403 ALL_LOCATIONS_REQUIRED` otherwise)."
)

warehouse_schema = extend_schema_view(
    list=extend_schema(
        tags=["Warehouses"],
        summary="List locations",
        description=_WAREHOUSE_LIST_DESCRIPTION,
        parameters=[ORGANIZATION_PATH_PARAM],
    ),
    create=extend_schema(
        tags=["Warehouses"],
        summary="Create a location",
        description=(
            "Counts against the plan's location limit (`403 PLAN_LIMIT_REACHED`). "
            "`is_default` can't be set here; use `set-default`."
            + _ORG_WIDE_NOTE
        ),
        parameters=[ORGANIZATION_PATH_PARAM],
    ),
    retrieve=extend_schema(
        tags=["Warehouses"],
        summary="Retrieve a location",
        description="404 for a location the caller can't access.",
        parameters=[ORGANIZATION_PATH_PARAM],
    ),
    update=extend_schema(
        tags=["Warehouses"],
        summary="Update a location",
        description=(
            "Setting `is_active=false` retires the location and frees a plan slot. "
            "The default location can't be deactivated (`409 DEFAULT_LOCATION_PROTECTED`)."
        ),
        parameters=[ORGANIZATION_PATH_PARAM],
    ),
    partial_update=extend_schema(
        tags=["Warehouses"],
        summary="Partially update a location",
        description=(
            "Setting `is_active=false` retires the location and frees a plan slot. "
            "The default location can't be deactivated (`409 DEFAULT_LOCATION_PROTECTED`)."
        ),
        parameters=[ORGANIZATION_PATH_PARAM],
    ),
    destroy=extend_schema(
        tags=["Warehouses"],
        summary="Delete a location",
        description=(
            "Prefer deactivating: deleting is refused while records still reference the "
            "location. The default location can't be deleted (`409 DEFAULT_LOCATION_PROTECTED`)."
        ),
        parameters=[ORGANIZATION_PATH_PARAM],
    ),
)

SET_DEFAULT_DESCRIPTION = (
    "Makes this location the organization's default (main) location and unsets the "
    "previous one. The default location can't be deactivated or deleted. "
    "`400 LOCATION_INACTIVE` if the location is inactive." + _ORG_WIDE_NOTE
)

stock_location_schema = extend_schema_view(
    list=extend_schema(
        tags=["Stock Locations"],
        summary="List storage areas (shelves, racks, bins)",
        description=(
            "Storage areas inside the caller's locations. Note: despite the URL, these "
            "are *storage areas* (PRD §35: Location > Storage Area), not branches; "
            "branches are `/warehouses/`. Filter by branch with `?location=`.\n\n"
            + LOCATION_ACCESS_NOTE
        ),
        parameters=[ORGANIZATION_PATH_PARAM, LOCATION_FILTER_PARAM],
    ),
    create=extend_schema(
        tags=["Stock Locations"],
        summary="Create a storage area",
        description="`warehouse` must be a location the caller can access (`403 LOCATION_ACCESS_DENIED`).",
        parameters=[ORGANIZATION_PATH_PARAM],
    ),
    retrieve=extend_schema(tags=["Stock Locations"], summary="Retrieve a storage area", parameters=[ORGANIZATION_PATH_PARAM]),
    update=extend_schema(tags=["Stock Locations"], summary="Update a storage area", parameters=[ORGANIZATION_PATH_PARAM]),
    partial_update=extend_schema(tags=["Stock Locations"], summary="Partially update a storage area", parameters=[ORGANIZATION_PATH_PARAM]),
    destroy=extend_schema(tags=["Stock Locations"], summary="Delete a storage area", parameters=[ORGANIZATION_PATH_PARAM]),
)
