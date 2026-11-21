from __future__ import annotations

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample
from drf_spectacular.utils import OpenApiParameter
from drf_spectacular.utils import OpenApiResponse
from drf_spectacular.utils import extend_schema
from drf_spectacular.utils import extend_schema_view

from core.applications.sales.api.cart_serializers import CartCheckoutSerializer
from core.applications.sales.api.cart_serializers import CartHoldSerializer
from core.applications.sales.api.cart_serializers import CartItemAddSerializer
from core.applications.sales.api.cart_serializers import CartItemUpdateSerializer
from core.applications.sales.api.cart_serializers import CartOpenSerializer
from core.applications.sales.api.cart_serializers import CartSerializer
from core.applications.sales.api.cart_serializers import CartSummarySerializer
from core.applications.sales.api.cart_serializers import CartUpdateSerializer
from core.applications.sales.api.serializers import SaleSerializer

TAGS = ["Carts"]
PERMISSION = "**Permission:** `CREATE_SALE`. You only ever see and change your own carts.\n\n"
RETURNS_CART = (
    "**Response:** the whole cart, priced again by the server (lines, tax, totals, stock, `issues`). "
    "Render it as is; never calculate totals in the frontend.\n\n"
)

_CART_EXAMPLE = {
    "id": "0b7c5e9a-2f41-4d8e-9c3a-6e1f2a4b5c6d",
    "status": "active",
    "version": 5,
    "label": "",
    "notes": "",
    "location": {"id": "495e06a8-d874-489e-96c9-9fc9c10a450e", "name": "Lekki"},
    "customer": {"id": "c41a0e6b-9d2f-4b7a-8e3c-1f5a6b7c8d9e", "name": "Walk-in Customer", "is_walk_in": True},
    "lines": [
        {
            "id": "7d1e3f5a-9b2c-4d6e-8f0a-1b3c5d7e9f2a",
            "product": {"id": "a2c4e6f8-1b3d-4f5a-9c7e-2d4f6a8b0c1e", "name": "Coca-Cola 50cl", "sku": "CC5-7K2Q9M"},
            "variant": None,
            "unit_name": "carton",
            "quantity": 2,
            "unit_price": "3600.00",
            "catalog_price": "3600.00",
            "price_overridden": False,
            "subtotal": "7200.00",
            "discount": "0.00",
            "tax": "540.00",
            "tax_rate": "7.5000",
            "total": "7740.00",
            "available_quantity": 14,
            "in_stock": True,
        }
    ],
    "item_count": 2,
    "subtotal": "7200.00",
    "discount_amount": "0.00",
    "cart_discount": "0.00",
    "tax_amount": "540.00",
    "total": "7740.00",
    "prices_include_tax": False,
    "checkout_ready": True,
    "issues": [],
    "held_at": None,
    "updated_at": "2026-11-20T10:15:00Z",
}

_OVERVIEW = """
**How a sale is built (point of sale)**

1. `POST /carts/` with `{"location": "<id>"}` opens your till's cart, or returns it if it's already open (`201` new, `200` existing). Call it when the sales screen loads: the cart survives refreshes, logouts and other devices.
2. `POST /carts/{id}/items/` adds products. Adding the same product, variant and unit again increases its quantity.
3. `PATCH /carts/{id}/items/{item_id}/` changes a quantity (0 removes the line), discount or price. `DELETE` removes it.
4. `PATCH /carts/{id}/` sets the customer, a cart discount, a label or notes. A new cart starts with the organization's **Walk-in Customer** (`customer.is_walk_in: true`), so an anonymous counter sale needs no customer step. Pick a registered customer only when you know the buyer, or to sell on credit.
5. `POST /carts/{id}/checkout/` with an optional `payment` creates the sale, invoice and receipt in one atomic step.

Serving someone else meanwhile: `POST /carts/{id}/hold/`, then `POST /carts/` gives a fresh cart; later `GET /carts/?status=held` and `POST /carts/{id}/resume/`.

**Rules**
* Prices, tax and totals are always worked out by the server with the same code as checkout, so the total shown is the total charged.
* Stock is **checked, not reserved**. Each line shows `available_quantity` and `in_stock`; stock only leaves at checkout, where it is checked again under lock. An abandoned cart never holds stock.
* `issues` lists what would stop checkout (`CART_EMPTY`, `OUT_OF_STOCK`, `PRODUCT_UNAVAILABLE`, `PRICING_ERROR`). `checkout_ready` is true when it's empty: disable the Pay button while it is false and show each issue's `message` (an issue with `item_id` belongs to that line).
* The **Walk-in Customer must pay in full** at checkout (`400 WALK_IN_MUST_PAY_IN_FULL`). Selling on credit needs a registered customer.
* One active cart per member per location, up to 20 held carts, 200 lines per cart.
* Idle open carts are abandoned after 7 days (`CART_EXPIRY_DAYS`).
* `version` goes up on every change; if two devices edit one cart, each change is applied in turn and the latest `version` wins on screen.
"""

_CART_ERRORS = {
    403: OpenApiResponse(description="`DISCOUNT_NOT_ALLOWED` / `PRICE_OVERRIDE_NOT_ALLOWED` or missing `CREATE_SALE`."),
    404: OpenApiResponse(description="`CART_NOT_FOUND`, `CART_ITEM_NOT_FOUND`, `PRODUCT_NOT_FOUND`, `VARIANT_NOT_FOUND`."),
    409: OpenApiResponse(description="`CART_CLOSED` (checked out or discarded), `CART_FULL`, `PRODUCT_UNAVAILABLE`."),
}

cart_schema = extend_schema_view(
    list=extend_schema(
        tags=TAGS,
        summary="List my carts",
        description=(
            "Your open carts (active and held) by default, most recently used first, paginated. Use "
            "`?status=held` for the held-carts screen. Rows are not priced; open one to see totals.\n\n" + PERMISSION
        ),
        parameters=[
            OpenApiParameter("status", OpenApiTypes.STR, description="`active`, `held`, `checked_out` or `abandoned`."),
            OpenApiParameter("location", OpenApiTypes.UUID, description="Only carts of this location."),
        ],
        responses={200: CartSummarySerializer(many=True)},
    ),
    create=extend_schema(
        tags=TAGS,
        summary="Open (or get) my cart at a location",
        description=_OVERVIEW + "\n" + PERMISSION + RETURNS_CART
        + "`location` is required if you only work in some locations (`400 LOCATION_REQUIRED`), and must be one "
        "of yours (`403 LOCATION_ACCESS_DENIED`).",
        request=CartOpenSerializer,
        examples=[OpenApiExample("Open", request_only=True, value={"location": "495e06a8-d874-489e-96c9-9fc9c10a450e"})],
        responses={201: CartSerializer, 200: CartSerializer},
    ),
    retrieve=extend_schema(
        tags=TAGS, summary="Cart with current prices", description=PERMISSION + RETURNS_CART,
        responses={200: CartSerializer}, examples=[OpenApiExample("Cart", value=_CART_EXAMPLE, response_only=True)],
    ),
    partial_update=extend_schema(
        tags=TAGS,
        summary="Set customer, cart discount, label or notes",
        description=(
            "Only the fields you send change. `customer_id: null` switches back to the Walk-in Customer. A cart "
            "discount needs "
            "`APPLY_DISCOUNT` and can't be combined with line discounts or exceed the subtotal (`400`).\n\n"
            + PERMISSION + RETURNS_CART
        ),
        request=CartUpdateSerializer,
        examples=[OpenApiExample("Customer", request_only=True, value={"customer_id": "c41a0e6b-9d2f-4b7a-8e3c-1f5a6b7c8d9e"})],
        responses={200: CartSerializer, **_CART_ERRORS},
    ),
    destroy=extend_schema(
        tags=TAGS,
        summary="Discard a cart",
        description="Abandons an open cart. Nothing was reserved, so nothing is undone.\n\n" + PERMISSION,
        responses={204: None, 409: OpenApiResponse(description="`CART_CLOSED`.")},
    ),
    add_item=extend_schema(
        tags=TAGS,
        summary="Add a product",
        description=(
            "Adds `quantity` (default 1) of a product. The same product, variant and unit already in the cart is "
            "increased instead of added twice; `discount` / `unit_price`, if sent, replace that line's values.\n\n"
            "* `unit_name`: sell in another unit, e.g. `carton` (must be the base unit or one of the product's unit "
            "conversions, else `400`).\n"
            "* `discount` needs `APPLY_DISCOUNT`; `unit_price` different from the catalog price needs "
            "`EDIT_SELLING_PRICE` (`403`).\n"
            "* Inactive or discontinued products are `409 PRODUCT_UNAVAILABLE`.\n"
            "* Being out of stock does **not** block adding; the line shows `in_stock: false` and checkout is "
            "blocked until it's fixed.\n\n" + PERMISSION + RETURNS_CART
        ),
        request=CartItemAddSerializer,
        examples=[
            OpenApiExample("Scan a product", request_only=True, value={"product_id": "a2c4e6f8-1b3d-4f5a-9c7e-2d4f6a8b0c1e"}),
            OpenApiExample(
                "Two cartons", request_only=True,
                value={"product_id": "a2c4e6f8-1b3d-4f5a-9c7e-2d4f6a8b0c1e", "quantity": 2, "unit_name": "carton"},
            ),
        ],
        responses={200: CartSerializer, **_CART_ERRORS},
    ),
    item=extend_schema(
        tags=TAGS,
        summary="Change or remove a line",
        description=(
            "`PATCH`: set `quantity` (0 removes the line), `discount` or `unit_price` (`null` = back to the "
            "catalog price). `DELETE`: remove the line.\n\n" + PERMISSION + RETURNS_CART
        ),
        request=CartItemUpdateSerializer,
        examples=[OpenApiExample("Quantity", request_only=True, value={"quantity": 3})],
        responses={200: CartSerializer, **_CART_ERRORS},
    ),
    clear=extend_schema(
        tags=TAGS, summary="Remove all lines",
        description="Empties the cart and its cart discount; keeps the customer and location.\n\n" + PERMISSION + RETURNS_CART,
        request=None, responses={200: CartSerializer},
    ),
    hold=extend_schema(
        tags=TAGS,
        summary="Hold (park) a cart",
        description=(
            "Parks the cart so you can serve someone else; the next `POST /carts/` gives you a fresh one. Give it a "
            "`label` (e.g. \"Table 4\") to find it later. An empty cart can't be held (`400 CART_EMPTY`); at most "
            "20 held carts (`409 HELD_CART_LIMIT`).\n\n" + PERMISSION + RETURNS_CART
        ),
        request=CartHoldSerializer,
        examples=[OpenApiExample("Hold", request_only=True, value={"label": "Mrs Ade"})],
        responses={200: CartSerializer, **_CART_ERRORS},
    ),
    resume=extend_schema(
        tags=TAGS,
        summary="Resume a held cart",
        description=(
            "Makes the held cart your active one at its location. The cart that was active there is held in its "
            "place, or deleted if it was empty, so nothing is lost.\n\n" + PERMISSION + RETURNS_CART
        ),
        request=None,
        responses={200: CartSerializer, **_CART_ERRORS},
    ),
    checkout=extend_schema(
        tags=TAGS,
        summary="Check out: turn the cart into a sale",
        description=(
            "Creates the sale exactly like `POST /sales/` (stock checked and deducted under lock, invoice issued, "
            "payment and receipt recorded, ledger and audit), all in one atomic step, from the cart's lines, "
            "customer, location, discount and notes. The cart becomes `checked_out`.\n\n"
            "* `payment` left out = sold on credit (the customer's credit rules apply). Add later payments with "
            "`POST /payments/`.\n"
            "* **Walk-in Customer** (every new cart's default): the payment must cover the total, else "
            "`400 WALK_IN_MUST_PAY_IN_FULL`. Choose a registered customer to sell on credit.\n"
            "* Cash above the total returns `change_due` on the sale.\n"
            "* **Safe to retry:** checking out a cart that is already checked out returns the same sale with `200` "
            "instead of selling twice.\n"
            "* Blocked with `400 CART_EMPTY` / `WALK_IN_MUST_PAY_IN_FULL`, `409 PRODUCT_UNAVAILABLE`, or a `400` when "
            "stock ran out since it was added. On any error nothing changes and the cart stays open.\n"
            "* Discount and price-override rights are checked again here.\n\n" + PERMISSION
            + "**Response:** the created sale (`201`), same body as `GET /sales/{id}/`."
        ),
        request=CartCheckoutSerializer,
        examples=[
            OpenApiExample("Cash", request_only=True, value={"payment": {"method": "cash", "amount": "10000.00"}}),
            OpenApiExample("On credit", request_only=True, value={"due_date": "2026-12-20"}),
        ],
        responses={
            201: SaleSerializer,
            200: OpenApiResponse(SaleSerializer, description="Already checked out: the same sale."),
            **_CART_ERRORS,
        },
    ),
)
