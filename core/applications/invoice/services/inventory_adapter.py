from __future__ import annotations

from typing import Any

from core.applications.invoice.exceptions import InsufficientStockError


def _actor(invoice: Any, field: str) -> Any:
    return getattr(invoice, field, None) or getattr(invoice, "created_by", None)


def deduct_stock_for_invoice(invoice: Any, lines: Any) -> None:
    # Imported here, not at module level: the inventory app loads while
    # AppConfig.ready() registers the invoice ports (same reason as sales_adapters).
    from core.applications.inventory.services import InsufficientStock
    from core.applications.inventory.services import process_invoice_stock_deduction
    from core.applications.products.models import Product

    ids = {str(line.product_id) for line in lines}
    products = {
        str(p.pk): p
        for p in Product.objects.filter(organization_id=invoice.organization_id, pk__in=ids)
    }
    prepared = sorted(
        (
            (products[str(line.product_id)], getattr(line, "variant_id", None), line.quantity)
            for line in lines
        ),
        key=lambda item: (str(item[0].pk), str(item[1] or "")),
    )
    try:
        process_invoice_stock_deduction(invoice, prepared, actor=_actor(invoice, "confirmed_by"))
    except InsufficientStock as exc:
        raise InsufficientStockError(
            str(exc),
            detail={
                "product_id": str(exc.product.pk),
                "requested": str(exc.requested),
                "available": str(exc.available),
            },
        ) from exc


def restore_stock_for_invoice(invoice: Any, reason: str = "") -> None:
    from core.applications.inventory.services import reverse_invoice_stock_deduction

    reverse_invoice_stock_deduction(
        invoice,
        actor=_actor(invoice, "voided_by"),
        reason=reason or getattr(invoice, "void_reason", "") or "",
    )
