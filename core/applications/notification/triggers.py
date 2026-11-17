"""
Business events -> notifications. Services call these one-liners; all the
"who, when, how often" rules live here and in dispatch.py.

Everything runs after the surrounding transaction commits and never raises
into the caller (see dispatch.py).
"""

from __future__ import annotations

import logging
import uuid
from decimal import Decimal

from django.core.cache import cache
from django.db import transaction
from django.db.models import Sum

from core.applications.notification import events as ev
from core.applications.notification.dispatch import notify

logger = logging.getLogger("notifications")

FAILED_LOGIN_WINDOW_SECONDS = 15 * 60


def _after_commit(func, *args, **kwargs):
    def run():
        try:
            func(*args, **kwargs)
        except Exception:  # noqa: BLE001 - alerts must never break the business action
            logger.exception("notification.trigger_failed", extra={"trigger": func.__name__})

    transaction.on_commit(run)


def _money(amount, organization) -> str:
    currency = (getattr(organization, "currency", "") or "NGN").upper()
    symbol = {"NGN": "₦", "USD": "$", "GBP": "£", "EUR": "€"}.get(currency, f"{currency} ")
    amount = Decimal(amount).quantize(Decimal("0.01"))
    return f"{symbol}{amount:,.0f}" if amount == amount.to_integral() else f"{symbol}{amount:,.2f}"


def _key(*ids) -> str:
    """Stable item key whatever the id format (hex or dashed UUID)."""
    return ":".join(uuid.UUID(str(i)).hex for i in ids)


def _name(user) -> str:
    return (getattr(user, "name", "") or getattr(user, "email", "") or "Someone") if user else "Someone"


# --------------------------------------------------------------------------- #
# Inventory
# --------------------------------------------------------------------------- #
def stock_levels_changed(warehouse, product_ids) -> None:
    """After stock left a branch: alert on products now out of stock or at/below their minimum."""
    if warehouse is None or not product_ids:
        return
    _after_commit(_check_stock_levels, warehouse, list({str(pid) for pid in product_ids}))


def _check_stock_levels(warehouse, product_ids) -> None:
    from core.applications.inventory.models import Inventory
    from core.applications.products.models import Product
    from core.helper.enums import ProductStatusChoices

    on_hand = dict(
        Inventory.objects.filter(warehouse=warehouse, product_id__in=product_ids)
        .values_list("product_id").annotate(total=Sum("quantity"))
    )
    products = Product.objects.filter(
        pk__in=product_ids, track_inventory=True, status=ProductStatusChoices.ACTIVE
    )
    for product in products:
        stock = on_hand.get(product.pk, 0) or 0
        link = {"resource": "products", "query": {"location": str(warehouse.pk), "low_stock": "true"}}
        data = {"product_id": str(product.pk), "stock": stock, "minimum_stock_level": product.minimum_stock_level}
        if stock <= 0:
            notify(ev.OUT_OF_STOCK, product.organization, title="Out of Stock",
                   message=f"{product.name} is out of stock at {warehouse.name}.",
                   location=warehouse, reference=product, link=link, data=data,
                   dedupe_key=_key(warehouse.pk, product.pk), group_item=_key(warehouse.pk, product.pk))
        elif stock <= product.minimum_stock_level:
            notify(ev.LOW_STOCK, product.organization, title="Low Stock",
                   message=(f"{product.name} is low at {warehouse.name}: {stock} left "
                            f"(minimum {product.minimum_stock_level})."),
                   location=warehouse, reference=product, link=link, data=data,
                   dedupe_key=_key(warehouse.pk, product.pk), group_item=_key(warehouse.pk, product.pk))


def stock_count_finalized(count, *, actor, variances: int) -> None:
    if not variances:
        return
    warehouse = count.warehouse
    plural = "s" if variances != 1 else ""
    notify(ev.STOCK_VARIANCE, warehouse.organization, title="Stock Variance",
           message=f"Stock count at {warehouse.name} found {variances} product{plural} that didn't match the system.",
           location=warehouse, reference=count, exclude_user=actor,
           link={"resource": "inventory", "query": {"location": str(warehouse.pk)}},
           data={"variances": variances})


# --------------------------------------------------------------------------- #
# Approvals
# --------------------------------------------------------------------------- #
def adjustment_requested(adjustment) -> None:
    change = adjustment.requested_quantity_change
    notify(ev.PENDING_APPROVAL, adjustment.warehouse.organization, title="Pending Approval",
           message=(f"{_name(adjustment.requested_by)} asked to adjust {adjustment.product.name} "
                    f"by {change:+d} at {adjustment.warehouse.name}."),
           location=adjustment.warehouse, reference=adjustment, exclude_user=adjustment.requested_by,
           link={"resource": "inventory/adjustments", "query": {"id": str(adjustment.pk)}},
           data={"adjustment_id": str(adjustment.pk), "quantity_change": change},
           group_item=_key(adjustment.pk))


def adjustment_decided(adjustment, *, actor, approved: bool) -> None:
    verb = "approved" if approved else "rejected"
    reason = "" if approved else f" Reason: {adjustment.rejection_reason}"
    notify(ev.APPROVAL_DECISION, adjustment.warehouse.organization, title=f"Adjustment {verb}",
           message=(f"{_name(actor)} {verb} the {adjustment.requested_quantity_change:+d} adjustment of "
                    f"{adjustment.product.name} at {adjustment.warehouse.name}.{reason}"),
           location=adjustment.warehouse, reference=adjustment,
           extra_users=[adjustment.requested_by], exclude_user=actor,
           link={"resource": "inventory/adjustments", "query": {"id": str(adjustment.pk)}},
           data={"adjustment_id": str(adjustment.pk), "decision": verb})


# --------------------------------------------------------------------------- #
# Sales
# --------------------------------------------------------------------------- #
def sale_completed(sale, *, actor) -> None:
    _after_commit(_check_large_sale, sale, actor)


def _check_large_sale(sale, actor) -> None:
    from core.applications.notification.preferences import setting_for

    setting = setting_for(sale.organization, ev.LARGE_SALE)
    threshold = Decimal(setting.options.get("threshold", 0))
    if not setting.enabled or sale.total < threshold:
        return
    branch = getattr(sale.warehouse, "name", "")
    notify(ev.LARGE_SALE, sale.organization, title="Large Sale",
           message=(f"{_name(actor)} made a sale of {_money(sale.total, sale.organization)}"
                    + (f" at {branch}." if branch else ".")),
           location=sale.warehouse, reference=sale, exclude_user=actor,
           link={"resource": "sales", "query": {"id": str(sale.pk)}},
           data={"sale_id": str(sale.pk), "total": str(sale.total), "threshold": str(threshold)})


def refund_processed(sale_return, *, actor) -> None:
    sale = sale_return.original_sale
    notify(ev.REFUND, sale_return.organization, title="Refund",
           message=(f"{_name(actor)} processed a return of {_money(sale_return.refund_amount, sale.organization)} "
                    f"on sale {sale.sale_id}."),
           location=sale.warehouse, reference=sale_return, exclude_user=actor,
           link={"resource": "sales/returns", "query": {"id": str(sale_return.pk)}},
           data={"sale_id": str(sale.pk), "refund_amount": str(sale_return.refund_amount)})


# --------------------------------------------------------------------------- #
# Security
# --------------------------------------------------------------------------- #
def permissions_changed(membership, *, actor, summary: str) -> None:
    notify(ev.PERMISSION_CHANGE, membership.organization, title="Permission Change",
           message=f"{_name(actor)} changed {_name(membership.user)}'s access: {summary}.",
           reference=membership, exclude_user=actor,
           link={"resource": "team", "query": {"member": str(membership.pk)}},
           data={"membership_id": str(membership.pk)})


def login_failed(user) -> None:
    """
    Count failed logins per account over 15 minutes. When an organization's
    threshold is reached, its Owners/Admins are told (once per hour per account).
    Login isn't tied to one organization, so every organization the user is an
    active member of is checked against its own threshold.
    """
    from core.applications.notification.preferences import setting_for
    from core.applications.users.models import Membership

    key = f"auth:failed-logins:{user.pk}"
    cache.add(key, 0, timeout=FAILED_LOGIN_WINDOW_SECONDS)
    try:
        attempts = cache.incr(key)
    except ValueError:  # expired between add and incr
        cache.set(key, 1, timeout=FAILED_LOGIN_WINDOW_SECONDS)
        attempts = 1

    for membership in Membership.objects.effective().filter(user=user).select_related("organization"):
        organization = membership.organization
        setting = setting_for(organization, ev.FAILED_LOGIN)
        if attempts < int(setting.options.get("attempts", 5)):
            continue
        notify(ev.FAILED_LOGIN, organization, title="Failed Logins",
               message=f"{attempts} failed login attempts on {user.email}'s account in the last 15 minutes.",
               reference=membership, exclude_user=user,
               link={"resource": "team", "query": {"member": str(membership.pk)}},
               data={"attempts": attempts, "user_id": user.pk},
               dedupe_key=f"user:{user.pk}", dedupe_hours=1)
