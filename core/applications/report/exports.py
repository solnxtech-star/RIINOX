"""
Structured report export payloads (JSON for frontend PDF rendering, plus optional CSV).
"""

from __future__ import annotations

import csv
import io
from typing import Any

from django.http import HttpResponse

from core.applications.report import services

EXPORTABLE_REPORT_TYPES = (
    "sales",
    "customers",
    "suppliers",
    "refunds",
    "payments",
    "inventory",
)

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _safe_csv_cell(value: Any) -> str:
    text = "" if value is None else str(value)
    if text.startswith(_FORMULA_PREFIXES):
        return f"'{text}"
    return text


def build_export_payload(
    report_type: str,
    ctx: services.ReportContext,
    granularity: str = "daily",
) -> dict[str, Any]:
    """Build full, un-truncated structured report data for frontend PDF/CSV rendering."""
    if report_type == "sales":
        return services.sales_report(ctx, granularity=granularity)
    if report_type == "customers":
        return services.customer_report(ctx)
    if report_type == "suppliers":
        return services.supplier_report(ctx)
    if report_type == "refunds":
        return services.refund_report(ctx, include_all_rows=True)
    if report_type == "payments":
        return services.payment_report(ctx, include_all_rows=True)
    if report_type == "inventory":
        return services.inventory_report(ctx, limit=services.MAX_PAGE_SIZE)
    raise ValueError(f"Unsupported export report type: {report_type}")


def _extract_csv_table(report_type: str, data: dict[str, Any]) -> tuple[list[str], list[list[Any]]]:
    if report_type == "sales":
        headers = ["Date / Period", "Start", "End", "Sales Amount"]
        rows = [
            [point["label"], point["start"], point["end"], point["value"]]
            for point in data["sales_over_time"]["points"]
        ]
        return headers, rows

    if report_type == "customers":
        headers = ["Customer", "Total Sales", "Outstanding Balance"]
        rows = [
            [item["name"], item["total_sales"], item["outstanding"]]
            for item in data["outstanding_customer_balances"]["items"]
        ]
        return headers, rows

    if report_type == "suppliers":
        headers = ["Supplier", "Spending"]
        rows = [
            [item["name"], item["amount"]]
            for item in data["spending_by_supplier"]["items"]
        ]
        return headers, rows

    if report_type == "refunds":
        headers = ["Ref", "Invoice", "Reason", "Amount", "Date"]
        rows = [
            [item["ref"], item["invoice_number"], item["reason"], item["amount"], item["date"]]
            for item in data["refunds"]["items"]
        ]
        return headers, rows

    if report_type == "payments":
        headers = ["Payment ID", "Date", "Method", "Amount"]
        rows = [
            [item["payment_id"], item["date"], item["method_display"], item["amount"]]
            for item in data["payments"]["items"]
        ]
        return headers, rows

    # inventory
    headers = ["Category", "Stock Value", "Share (%)"]
    rows = [
        [item["name"], item["value"], item["percentage"]]
        for item in data["stock_value_by_category"]["items"]
    ]
    return headers, rows


def export_report_csv(report_type: str, data: dict[str, Any], filename_prefix: str) -> HttpResponse:
    """Render report data as a formula-safe CSV attachment."""
    headers, rows = _extract_csv_table(report_type, data)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([_safe_csv_cell(header) for header in headers])
    for row in rows:
        writer.writerow([_safe_csv_cell(cell) for cell in row])

    response = HttpResponse(buffer.getvalue(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename_prefix}-{report_type}-report.csv"'
    return response
