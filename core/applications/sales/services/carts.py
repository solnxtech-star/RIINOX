"""
Server-side carts (point-of-sale baskets) and their checkout into a Sale.

Rules that keep carts honest and cheap:

* A cart stores what is being sold (product, variant, unit, quantity, discount,
  optional price override), never computed prices. `price_cart` prices it with
  `_build_lines`, the exact code `create_sale` uses, so the total on screen is
  the total charged (PRD §39: the backend prices).
* Stock is checked, not reserved. Each line reports what the location has;
  stock only leaves at checkout, inside the sale's own transaction (PRD §22, §43).
  An abandoned cart therefore never locks stock.
* Every change locks the cart row (`select_for_update`), so two tabs or devices
  editing one cart apply one after the other. `version` goes up on each change.
* Each member has one active cart per location (enforced by a database
  constraint) and up to MAX_HELD_CARTS held ones. Idle open carts expire after
  CART_EXPIRY_DAYS and are deleted CART_PURGE_DAYS later (`expire_idle_carts`).
* Checkout is idempotent: once a cart is checked out, checking it out again
  returns the same sale instead of selling twice.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import ROUND_FLOOR, Decimal
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone
from rest_framework import status

from core.applications.customers.walk_in import get_walk_in_customer
from core.applications.invoice.services.invoices import load_active_customer
from core.applications.sales.models import (
    OPEN_CART_STATUSES,
    Cart,
    CartItem,
    CartStatus,
    Sale,
)
from core.applications.sales.services.exceptions import SaleError, SaleValidationError
from core.applications.sales.services.sales import (
    _build_lines,
    _money,
    _unit_multiplier,
    catalog_unit_price,
    create_sale,
)
from core.helper.enums import PermissionCode, ProductStatusChoices

if TYPE_CHECKING:
    from core.applications.users.models import Organization
    from core.applications.warehouse.access import LocationScope

logger = logging.getLogger(__name__)

MAX_ITEMS_PER_CART = 200
MAX_HELD_CARTS = 20
ZERO = Decimal("0.00")

__all__ = [
    "MAX_HELD_CARTS",
    "MAX_ITEMS_PER_CART",
    "CartError",
    "PricedCart",
    "add_item",
    "checkout_cart",
    "clear_cart",
    "discard_cart",
    "expire_idle_carts",
    "get_cart",
    "hold_cart",
    "open_cart",
    "price_cart",
    "remove_item",
    "resume_cart",
    "update_cart",
    "update_item",
]


class CartError(SaleError):
    """A cart rule was broken. `code` is what the frontend switches on."""

    code = "CART_ERROR"
    default_message = "The cart operation could not be completed."


def _fail(code: str, message: str, http_status: int = status.HTTP_400_BAD_REQUEST, **detail: Any) -> CartError:
    return CartError(message, code=code, status_code=http_status, detail={k: str(v) for k, v in detail.items()})


# --------------------------------------------------------------------------- #
# Pricing (read model)
# --------------------------------------------------------------------------- #
@dataclass
class PricedLine:
    item: CartItem
    unit_price: Decimal
    catalog_price: Decimal | None
    price_overridden: bool
    subtotal: Decimal
    discount: Decimal
    tax: Decimal
    tax_rate: Decimal
    total: Decimal
    available_quantity: int | None  # in this line's unit; None for products that don't track stock
    in_stock: bool = True


@dataclass
class PricedCart:
    cart: Cart
    lines: list[PricedLine]
    subtotal: Decimal = ZERO
    discount_amount: Decimal = ZERO
    tax_amount: Decimal = ZERO
    total: Decimal = ZERO
    prices_include_tax: bool = False
    issues: list[dict[str, str]] = field(default_factory=list)

    @property
    def item_count(self) -> int:
        return sum(line.item.quantity for line in self.lines)

    @property
    def checkout_ready(self) -> bool:
        return not self.issues


def _load_items(cart: Cart) -> list[CartItem]:
    return list(
        cart.items.select_related("product", "variant")
        .prefetch_related("product__unit_conversions")
        .order_by("created_at")
    )


def _item_input(item: CartItem) -> dict[str, Any]:
    return {
        "product": item.product,
        "variant": item.variant,
        "quantity": item.quantity,
        "unit_name": item.unit_name,
        "discount": item.discount,
        "unit_price": item.unit_price,
    }


def _available_base_units(cart: Cart, items: list[CartItem]) -> dict[tuple[Any, Any], Decimal]:
    """On-hand base units per (product, variant) at the cart's location, in one query."""
    product_ids = {item.product_id for item in items if item.product.track_inventory}
    if not product_ids:
        return {}
    from core.applications.inventory.models import Inventory

    rows = (
        Inventory.objects.filter(warehouse_id=cart.warehouse_id, product_id__in=product_ids)
        .values("product_id", "variant_id")
        .annotate(on_hand=Sum("quantity"))
    )
    return {(row["product_id"], row["variant_id"]): Decimal(row["on_hand"] or 0) for row in rows}


def price_cart(cart: Cart, items: list[CartItem] | None = None) -> PricedCart:
    """
    The cart with current prices, tax, totals, stock per line and anything that
    would stop checkout (`issues`). Never raises for pricing problems: a price or
    product changed after the item was added is reported, not fatal.
    """
    items = _load_items(cart) if items is None else items
    priced = PricedCart(cart=cart, lines=[])
    if not items:
        priced.issues.append({"code": "CART_EMPTY", "message": "Add at least one product."})
    if not items:
        return priced

    try:
        lines, totals, _overrides = _build_lines(
            cart.organization, [_item_input(item) for item in items], cart.discount_amount, 0
        )
    except SaleValidationError as exc:
        priced.issues.append({"code": "PRICING_ERROR", "message": exc.message})
        lines, totals = None, None

    available = _available_base_units(cart, items)
    needed: dict[tuple[Any, Any], Decimal] = defaultdict(Decimal)
    multipliers = []
    for item, line in zip(items, lines or [None] * len(items), strict=True):
        multiplier = line["unit_multiplier"] if line else _safe_multiplier(item)
        multipliers.append(multiplier)
        needed[(item.product_id, item.variant_id)] += item.quantity * multiplier

    for index, item in enumerate(items):
        line = lines[index] if lines else None
        catalog = catalog_unit_price(item.product, item.unit_name, multipliers[index])
        unit_price = line["unit_price"] if line else (item.unit_price if item.unit_price is not None else (catalog or ZERO))
        subtotal = unit_price * item.quantity
        discount = line["discount"] if line else item.discount
        tax = line["tax"] if line else ZERO
        include = totals["prices_include_tax"] if totals else False
        key = (item.product_id, item.variant_id)

        available_quantity = None
        in_stock = True
        if item.product.track_inventory:
            on_hand = available.get(key, ZERO)
            multiplier = multipliers[index] or Decimal(1)
            available_quantity = int((on_hand / multiplier).to_integral_value(rounding=ROUND_FLOOR))
            in_stock = needed[key] <= on_hand

        priced.lines.append(
            PricedLine(
                item=item,
                unit_price=unit_price,
                catalog_price=catalog,
                price_overridden=item.unit_price is not None and catalog is not None and item.unit_price != catalog,
                subtotal=subtotal,
                discount=discount,
                tax=tax,
                tax_rate=line["tax_rate"] if line else ZERO,
                total=(subtotal - discount) + (ZERO if include else tax),
                available_quantity=available_quantity,
                in_stock=in_stock,
            )
        )
        if not in_stock:
            priced.issues.append({
                "code": "OUT_OF_STOCK",
                "message": f"Only {available_quantity} {item.unit_name} of {item.product.name} available.",
                "item_id": str(item.pk),
            })
        if item.product.status != ProductStatusChoices.ACTIVE:
            priced.issues.append({
                "code": "PRODUCT_UNAVAILABLE",
                "message": f"{item.product.name} is no longer for sale. Remove it.",
                "item_id": str(item.pk),
            })

    if totals:
        priced.subtotal = totals["subtotal"]
        priced.discount_amount = totals["discount_amount"]
        priced.tax_amount = totals["tax_amount"]
        priced.total = totals["total"]
        priced.prices_include_tax = totals["prices_include_tax"]
    else:
        priced.subtotal = sum((line.subtotal for line in priced.lines), ZERO)
        priced.total = priced.subtotal
    return priced


def _catalog(item: CartItem) -> Decimal | None:
    """Catalog price of one of this line's unit."""
    return catalog_unit_price(item.product, item.unit_name, _safe_multiplier(item))


def _safe_multiplier(item: CartItem) -> Decimal:
    try:
        return _unit_multiplier(item.product, item.unit_name)
    except SaleValidationError:
        return Decimal(1)


# --------------------------------------------------------------------------- #
# Loading and locking
# --------------------------------------------------------------------------- #
def _carts(organization: Organization, actor: Any):
    """A member only ever sees and changes their own carts."""
    return Cart.objects.filter(organization=organization, created_by=actor)


def get_cart(organization: Organization, actor: Any, cart_id: Any) -> Cart:
    cart = _carts(organization, actor).select_related("customer", "warehouse").filter(pk=cart_id).first()
    if cart is None:
        raise _fail("CART_NOT_FOUND", "Cart not found.", status.HTTP_404_NOT_FOUND, cart_id=cart_id)
    return cart


def _lock_open(organization: Organization, actor: Any, cart_id: Any) -> Cart:
    cart = _carts(organization, actor).select_for_update().filter(pk=cart_id).first()
    if cart is None:
        raise _fail("CART_NOT_FOUND", "Cart not found.", status.HTTP_404_NOT_FOUND, cart_id=cart_id)
    if cart.status not in OPEN_CART_STATUSES:
        raise _fail(
            "CART_CLOSED", f"This cart is {cart.get_status_display().lower()} and can't be changed.",
            status.HTTP_409_CONFLICT, status=cart.status,
        )
    return cart


def _touch(cart: Cart, *fields: str) -> None:
    """Save a change, bump `version` and the idle clock, and check it still prices."""
    cart.version += 1
    cart.last_activity_at = timezone.now()
    cart.save(update_fields=[*fields, "version", "last_activity_at", "updated_at"])
    items = _load_items(cart)
    if items:
        # Raises (and rolls the change back) if the cart would no longer price,
        # e.g. a cart discount above the subtotal, or both cart and line discounts.
        _build_lines(cart.organization, [_item_input(item) for item in items], cart.discount_amount, 0)


def _require_permission(actor: Any, organization: Organization, code: str, error: str, message: str) -> None:
    from core.applications.users.services import has_org_permission

    if not has_org_permission(actor, organization, code):
        raise _fail(error, message, status.HTTP_403_FORBIDDEN)


def _check_discount_rights(actor: Any, organization: Organization) -> None:
    _require_permission(
        actor, organization, PermissionCode.APPLY_DISCOUNT,
        "DISCOUNT_NOT_ALLOWED", "Your role can't give discounts.",
    )


def _check_price_override_rights(actor: Any, organization: Organization) -> None:
    _require_permission(
        actor, organization, PermissionCode.EDIT_SELLING_PRICE,
        "PRICE_OVERRIDE_NOT_ALLOWED", "Your role can't change selling prices.",
    )


# --------------------------------------------------------------------------- #
# Opening, holding, resuming, discarding
# --------------------------------------------------------------------------- #
def _resolve_warehouse(organization: Organization, warehouse_id: Any, scope: LocationScope | None):
    from core.applications.warehouse.models import Warehouse

    warehouses = Warehouse.objects.filter(organization=organization, is_active=True)
    if warehouse_id:
        warehouse = warehouses.filter(pk=warehouse_id).first()
        if warehouse is None:
            raise _fail("LOCATION_NOT_FOUND", "Location not found.", status.HTTP_404_NOT_FOUND,
                        location_id=warehouse_id)
    else:
        if scope is not None and not scope.all_locations:
            raise _fail("LOCATION_REQUIRED", "Choose the location you are selling from.", location="required")
        warehouse = warehouses.order_by("-is_default", "name").first()
        if warehouse is None:
            raise _fail("LOCATION_REQUIRED", "Create a location before selling.", location="required")
    if scope is not None:
        scope.require(warehouse)
    return warehouse


@transaction.atomic
def open_cart(
    organization: Organization, actor: Any, *, warehouse_id: Any = None, scope: LocationScope | None = None
) -> tuple[Cart, bool]:
    """The member's active cart at this location, created if there is none. Returns (cart, created)."""
    warehouse = _resolve_warehouse(organization, warehouse_id, scope)
    existing = _carts(organization, actor).filter(warehouse=warehouse, status=CartStatus.ACTIVE).first()
    if existing is not None:
        return existing, False
    try:
        with transaction.atomic():
            cart = Cart.objects.create(
                organization=organization,
                created_by=actor,
                warehouse=warehouse,
                customer=get_walk_in_customer(organization),  # anonymous until a customer is chosen
            )
    except IntegrityError:
        # Two requests opened the till at once; the constraint let one win.
        return _carts(organization, actor).get(warehouse=warehouse, status=CartStatus.ACTIVE), False
    return cart, True


@transaction.atomic
def update_cart(organization: Organization, actor: Any, cart_id: Any, changes: dict[str, Any]) -> Cart:
    """Change the customer, label, notes or cart discount. Only the keys given change."""
    cart = _lock_open(organization, actor, cart_id)
    fields = []
    if "customer_id" in changes:
        customer_id = changes["customer_id"]
        # null goes back to the Walk-in Customer: a cart always has someone to sell to.
        cart.customer = (
            load_active_customer(organization, customer_id) if customer_id else get_walk_in_customer(organization)
        )
        fields.append("customer")
    if "discount_amount" in changes:
        discount = _money(changes["discount_amount"] or 0, field="discount")
        if discount > 0:
            _check_discount_rights(actor, organization)
        cart.discount_amount = discount
        fields.append("discount_amount")
    for name in ("label", "notes"):
        if name in changes:
            setattr(cart, name, (changes[name] or "").strip())
            fields.append(name)
    _touch(cart, *fields)
    return cart


@transaction.atomic
def hold_cart(organization: Organization, actor: Any, cart_id: Any, *, label: str | None = None) -> Cart:
    """Park the cart to serve someone else. The till gets a fresh active cart on the next open."""
    cart = _lock_open(organization, actor, cart_id)
    if cart.status == CartStatus.HELD:
        return cart
    if not cart.items.exists():
        raise _fail("CART_EMPTY", "An empty cart can't be held.")
    held = _carts(organization, actor).filter(status=CartStatus.HELD).count()
    if held >= MAX_HELD_CARTS:
        raise _fail("HELD_CART_LIMIT", f"You already have {MAX_HELD_CARTS} held carts. Finish or discard one.",
                    status.HTTP_409_CONFLICT, limit=MAX_HELD_CARTS)
    cart.status = CartStatus.HELD
    cart.held_at = timezone.now()
    if label is not None:
        cart.label = label.strip()
    _touch(cart, "status", "held_at", "label")
    return cart


@transaction.atomic
def resume_cart(organization: Organization, actor: Any, cart_id: Any, *, scope: LocationScope | None = None) -> Cart:
    """
    Make a held cart the active one at its location. The cart that was active
    there is held in its place, or deleted if it was empty.
    """
    cart = _lock_open(organization, actor, cart_id)
    if cart.status == CartStatus.ACTIVE:
        return cart
    if scope is not None:
        scope.require(cart.warehouse)
    current = (
        _carts(organization, actor).select_for_update()
        .filter(warehouse_id=cart.warehouse_id, status=CartStatus.ACTIVE).first()
    )
    if current is not None:
        if current.items.exists():
            current.status = CartStatus.HELD
            current.held_at = timezone.now()
            current.version += 1
            current.save(update_fields=["status", "held_at", "version", "updated_at"])
        else:
            current.delete()
    cart.status = CartStatus.ACTIVE
    cart.held_at = None
    _touch(cart, "status", "held_at")
    return cart


@transaction.atomic
def discard_cart(organization: Organization, actor: Any, cart_id: Any) -> None:
    """Abandon an open cart. Nothing was reserved, so nothing needs undoing."""
    cart = _lock_open(organization, actor, cart_id)
    cart.status = CartStatus.ABANDONED
    cart.version += 1
    cart.save(update_fields=["status", "version", "updated_at"])


# --------------------------------------------------------------------------- #
# Items
# --------------------------------------------------------------------------- #
def _load_product(organization: Organization, product_id: Any, variant_id: Any):
    from core.applications.products.models import Product, ProductVariant

    product = Product.objects.filter(organization=organization, pk=product_id).first()
    if product is None:
        raise _fail("PRODUCT_NOT_FOUND", "Product not found.", status.HTTP_404_NOT_FOUND, product_id=product_id)
    if product.status != ProductStatusChoices.ACTIVE:
        raise _fail("PRODUCT_UNAVAILABLE", f"{product.name} is not for sale.", status.HTTP_409_CONFLICT,
                    product_id=product.pk)
    variant = None
    if variant_id:
        variant = ProductVariant.objects.filter(product=product, pk=variant_id).first()
        if variant is None:
            raise _fail("VARIANT_NOT_FOUND", "That variant does not belong to the product.",
                        status.HTTP_404_NOT_FOUND, variant_id=variant_id)
    return product, variant


def _apply_line_fields(item: CartItem, actor: Any, organization: Organization, values: dict[str, Any]) -> None:
    if "discount" in values:
        discount = _money(values["discount"] or 0, field="discount")
        if discount > 0:
            _check_discount_rights(actor, organization)
        item.discount = discount
    if "unit_price" in values:
        price = values["unit_price"]
        if price is not None:
            price = _money(price, field="unit price")
            if price != _catalog(item):
                _check_price_override_rights(actor, organization)
        item.unit_price = price


@transaction.atomic
def add_item(organization: Organization, actor: Any, cart_id: Any, values: dict[str, Any]) -> Cart:
    """
    Add a product. The same product, variant and unit already in the cart gets
    the quantity added to it (one line, not two); `discount` / `unit_price`, if
    sent, replace that line's values.
    """
    cart = _lock_open(organization, actor, cart_id)
    product, variant = _load_product(organization, values["product_id"], values.get("variant_id"))
    unit_name = values.get("unit_name") or product.unit_of_measurement
    _unit_multiplier(product, unit_name)  # 400 now if the unit isn't sold for this product

    item = cart.items.filter(product=product, variant=variant, unit_name=unit_name).first()
    if item is None:
        if cart.items.count() >= MAX_ITEMS_PER_CART:
            raise _fail("CART_FULL", f"A cart can hold at most {MAX_ITEMS_PER_CART} different lines.",
                        status.HTTP_409_CONFLICT, limit=MAX_ITEMS_PER_CART)
        item = CartItem(cart=cart, product=product, variant=variant, unit_name=unit_name, quantity=0)
    item.quantity += values["quantity"]
    _apply_line_fields(item, actor, organization, values)
    item.save()
    _touch(cart)
    return cart


def _lock_item(cart: Cart, item_id: Any) -> CartItem:
    item = cart.items.select_related("product").filter(pk=item_id).first()
    if item is None:
        raise _fail("CART_ITEM_NOT_FOUND", "That line is not in this cart.", status.HTTP_404_NOT_FOUND,
                    item_id=item_id)
    return item


@transaction.atomic
def update_item(organization: Organization, actor: Any, cart_id: Any, item_id: Any, values: dict[str, Any]) -> Cart:
    """Set a line's quantity (0 removes it), discount or price override."""
    cart = _lock_open(organization, actor, cart_id)
    item = _lock_item(cart, item_id)
    if values.get("quantity") == 0:
        item.delete()
    else:
        if "quantity" in values:
            item.quantity = values["quantity"]
        _apply_line_fields(item, actor, organization, values)
        item.save()
    _touch(cart)
    return cart


@transaction.atomic
def remove_item(organization: Organization, actor: Any, cart_id: Any, item_id: Any) -> Cart:
    cart = _lock_open(organization, actor, cart_id)
    _lock_item(cart, item_id).delete()
    _touch(cart)
    return cart


@transaction.atomic
def clear_cart(organization: Organization, actor: Any, cart_id: Any) -> Cart:
    """Remove every line and the cart discount; keeps the customer and location."""
    cart = _lock_open(organization, actor, cart_id)
    cart.items.all().delete()
    cart.discount_amount = ZERO
    _touch(cart, "discount_amount")
    return cart


# --------------------------------------------------------------------------- #
# Checkout
# --------------------------------------------------------------------------- #
@transaction.atomic
def checkout_cart(
    organization: Organization,
    actor: Any,
    cart_id: Any,
    *,
    payment: dict[str, Any] | None = None,
    due_date: Any = None,
    scope: LocationScope | None = None,
) -> tuple[Sale, bool]:
    """
    Turn the cart into a Sale with `create_sale` (stock check and deduction,
    invoice, payment, receipt, ledger and audit, all atomic). Returns
    (sale, replayed): a cart that was already checked out returns its sale.
    """
    cart = _carts(organization, actor).select_for_update().filter(pk=cart_id).first()
    if cart is None:
        raise _fail("CART_NOT_FOUND", "Cart not found.", status.HTTP_404_NOT_FOUND, cart_id=cart_id)
    if cart.status == CartStatus.CHECKED_OUT:
        return cart.sale, True
    if cart.status not in OPEN_CART_STATUSES:
        raise _fail("CART_CLOSED", "This cart was discarded.", status.HTTP_409_CONFLICT, status=cart.status)
    if scope is not None:
        scope.require(cart.warehouse)
    customer = cart.customer or get_walk_in_customer(organization)

    items = _load_items(cart)
    if not items:
        raise _fail("CART_EMPTY", "Add at least one product before checkout.")
    for item in items:
        if item.product.status != ProductStatusChoices.ACTIVE:
            raise _fail("PRODUCT_UNAVAILABLE", f"{item.product.name} is no longer for sale. Remove it.",
                        status.HTTP_409_CONFLICT, item_id=item.pk)
    # Rights can change while a cart is held, so check them again at the till.
    if cart.discount_amount > 0 or any(item.discount > 0 for item in items):
        _check_discount_rights(actor, organization)
    if any(item.unit_price is not None and item.unit_price != _catalog(item) for item in items):
        _check_price_override_rights(actor, organization)

    sale = create_sale(
        {
            "organization": organization,
            "customer": customer,
            "warehouse": cart.warehouse,
            "sales_rep": actor,
            "discount_amount": cart.discount_amount,
            "notes": cart.notes or None,
            "due_date": due_date,
            "payment": payment,
            "items": [
                {k: v for k, v in _item_input(item).items() if not (k == "unit_price" and v is None)}
                for item in items
            ],
        },
        actor=actor,
    )

    cart.status = CartStatus.CHECKED_OUT
    cart.sale = sale
    cart.checked_out_at = timezone.now()
    cart.version += 1
    cart.save(update_fields=["status", "sale", "checked_out_at", "version", "updated_at"])
    # The sale now holds the lines; keeping them twice would only grow the table.
    cart.items.all().delete()
    return sale, False


# --------------------------------------------------------------------------- #
# Housekeeping (Celery beat: sales.expire_idle_carts)
# --------------------------------------------------------------------------- #
def expire_idle_carts(*, now=None, batch_size: int = 1000) -> dict[str, int]:
    """
    Abandon open carts idle for CART_EXPIRY_DAYS, then delete abandoned carts
    CART_PURGE_DAYS after that. Checked-out carts are kept (they point at their
    sale). Works in bounded batches so a large backlog never holds long locks.
    """
    now = now or timezone.now()
    expiry = now - timedelta(days=getattr(settings, "CART_EXPIRY_DAYS", 7))
    purge = now - timedelta(days=getattr(settings, "CART_PURGE_DAYS", 30))

    expired = 0
    while True:
        ids = list(
            Cart.objects.filter(status__in=OPEN_CART_STATUSES, last_activity_at__lt=expiry)
            .values_list("pk", flat=True)[:batch_size]
        )
        if not ids:
            break
        expired += Cart.objects.filter(pk__in=ids, status__in=OPEN_CART_STATUSES).update(
            status=CartStatus.ABANDONED, updated_at=now
        )

    purged = 0
    while True:
        ids = list(
            Cart.objects.filter(status=CartStatus.ABANDONED, updated_at__lt=purge)
            .values_list("pk", flat=True)[:batch_size]
        )
        if not ids:
            break
        purged += Cart.objects.filter(pk__in=ids).delete()[1].get("sales.Cart", 0)

    if expired or purged:
        logger.info("carts.expired", extra={"expired": expired, "purged": purged})
    return {"expired": expired, "purged": purged}
