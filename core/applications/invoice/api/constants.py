"""
Limits and layout shared by the invoice viewset and its OpenAPI schema.

They live here, not in the viewset, so the schema module can quote the real
numbers in its documentation without importing the views (which import the
schema, so that would be a cycle).
"""

from __future__ import annotations

from typing import Final

__all__ = ["EXPORT_CHUNK_SIZE", "EXPORT_COLUMNS", "MAX_EXPORT_ROWS"]

# A bigger export belongs in a background job (PRD §51), not in a request.
MAX_EXPORT_ROWS: Final = 50_000
EXPORT_CHUNK_SIZE: Final = 200
EXPORT_COLUMNS: Final = (
    "Invoice",
    "Status",
    "Customer",
    "Issued",
    "Due",
    "Currency",
    "Total",
    "Paid",
    "Balance",
)
