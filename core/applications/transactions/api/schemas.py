from __future__ import annotations

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter
from drf_spectacular.utils import extend_schema
from drf_spectacular.utils import extend_schema_view

from core.applications.warehouse.api.schemas import LOCATION_ACCESS_NOTE
from core.applications.warehouse.api.schemas import LOCATION_FILTER_PARAM

ORGANIZATION_PATH_PARAM = OpenApiParameter(
    name="organization_id",
    type=OpenApiTypes.UUID,
    location=OpenApiParameter.PATH,
    description="UUID of the tenant organization owning the resource.",
)

_LIST_DESCRIPTION = (
    "The organization's business events, newest first: sales, returns, purchases, goods "
    "received, stock adjustments, transfers, counts and payments, each with its "
    "double-entry `financial_entries` (debits always equal credits).\n\n"
    "**Read-only.** Transactions are created by the operation itself (e.g. confirming a "
    "sale) and are never edited or deleted; a correction is a new, reversing transaction "
    "(PRD §44). There is no create, update or delete endpoint.\n\n"
    "* `location`: where the event happened. A transfer shows its **source** location.\n"
    "* `content_type` / `object_id`: the document that produced it (sale, invoice, payment...).\n\n"
    "Requires `VIEW_FINANCIAL_REPORTS`.\n\n" + LOCATION_ACCESS_NOTE
)

transaction_schema = extend_schema_view(
    list=extend_schema(
        tags=["Transactions"],
        summary="List transactions",
        description=_LIST_DESCRIPTION,
        parameters=[ORGANIZATION_PATH_PARAM, LOCATION_FILTER_PARAM],
    ),
    retrieve=extend_schema(
        tags=["Transactions"],
        summary="Retrieve a transaction",
        description="404 for a transaction of another organization or of a location the caller can't access.",
        parameters=[ORGANIZATION_PATH_PARAM],
    ),
)
