from __future__ import annotations

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema_view, extend_schema

ORGANIZATION_PATH_PARAM = OpenApiParameter(
    name="organization_id",
    type=OpenApiTypes.UUID,
    location=OpenApiParameter.PATH,
    description="UUID of the tenant organization owning the resource.",
)

warehouse_schema = extend_schema_view(
    list=extend_schema(tags=["Warehouses"], summary="List Warehouses", parameters=[ORGANIZATION_PATH_PARAM]),
    create=extend_schema(tags=["Warehouses"], summary="Create Warehouse", parameters=[ORGANIZATION_PATH_PARAM]),
    retrieve=extend_schema(tags=["Warehouses"], summary="Retrieve Warehouse", parameters=[ORGANIZATION_PATH_PARAM]),
    update=extend_schema(tags=["Warehouses"], summary="Update Warehouse", parameters=[ORGANIZATION_PATH_PARAM]),
    partial_update=extend_schema(tags=["Warehouses"], summary="Partial Update Warehouse", parameters=[ORGANIZATION_PATH_PARAM]),
    destroy=extend_schema(tags=["Warehouses"], summary="Delete Warehouse", parameters=[ORGANIZATION_PATH_PARAM]),
)

stock_location_schema = extend_schema_view(
    list=extend_schema(tags=["Stock Locations"], summary="List Stock Locations", parameters=[ORGANIZATION_PATH_PARAM]),
    create=extend_schema(tags=["Stock Locations"], summary="Create Stock Location", parameters=[ORGANIZATION_PATH_PARAM]),
    retrieve=extend_schema(tags=["Stock Locations"], summary="Retrieve Stock Location", parameters=[ORGANIZATION_PATH_PARAM]),
    update=extend_schema(tags=["Stock Locations"], summary="Update Stock Location", parameters=[ORGANIZATION_PATH_PARAM]),
    partial_update=extend_schema(tags=["Stock Locations"], summary="Partial Update Stock Location", parameters=[ORGANIZATION_PATH_PARAM]),
    destroy=extend_schema(tags=["Stock Locations"], summary="Delete Stock Location", parameters=[ORGANIZATION_PATH_PARAM]),
)
