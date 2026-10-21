from __future__ import annotations

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema_view, extend_schema

ORGANIZATION_PATH_PARAM = OpenApiParameter(
    name="organization_id",
    type=OpenApiTypes.UUID,
    location=OpenApiParameter.PATH,
    description="UUID of the tenant organization owning the resource.",
)


transaction_schema = extend_schema_view(
    list=extend_schema(tags=["Transactions"], summary="List Transactions", parameters=[ORGANIZATION_PATH_PARAM]),
    create=extend_schema(tags=["Transactions"], summary="Create Transaction", parameters=[ORGANIZATION_PATH_PARAM]),
    retrieve=extend_schema(tags=["Transactions"], summary="Retrieve Transaction", parameters=[ORGANIZATION_PATH_PARAM]),
    update=extend_schema(tags=["Transactions"], summary="Update Transaction", parameters=[ORGANIZATION_PATH_PARAM]),
    partial_update=extend_schema(tags=["Transactions"], summary="Partial Update Transaction", parameters=[ORGANIZATION_PATH_PARAM]),
    destroy=extend_schema(tags=["Transactions"], summary="Delete Transaction", parameters=[ORGANIZATION_PATH_PARAM]),
)
