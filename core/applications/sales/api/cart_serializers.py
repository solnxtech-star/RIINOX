from __future__ import annotations

from decimal import Decimal

from rest_framework import serializers

from core.applications.sales.models import CartStatus
from core.helper.serializer_fields import PaymentMethodField

MONEY = {"max_digits": 12, "decimal_places": 2}
MAX_QUANTITY = 1_000_000


class _StrictMixin:
    """Unknown keys are a 400, so a client never believes a field it sent was used."""

    def validate(self, attrs):
        initial = getattr(self, "initial_data", None)
        if hasattr(initial, "keys"):
            unknown = sorted(set(initial.keys()) - set(self.fields))
            if unknown:
                raise serializers.ValidationError({key: ["Unknown field."] for key in unknown})
        return super().validate(attrs)


# --------------------------------------------------------------------------- #
# Requests
# --------------------------------------------------------------------------- #
class CartOpenSerializer(_StrictMixin, serializers.Serializer):
    location = serializers.UUIDField(
        required=False,
        allow_null=True,
        help_text="Location (warehouse) id you are selling from. Optional if you work in all locations "
        "(the default location is used); required otherwise.",
    )


class CartUpdateSerializer(_StrictMixin, serializers.Serializer):
    customer_id = serializers.UUIDField(
        required=False, allow_null=True,
        help_text="A registered customer. null switches back to the Walk-in Customer.",
    )
    discount_amount = serializers.DecimalField(
        required=False, min_value=Decimal("0"), **MONEY,
        help_text="Discount on the whole cart (needs APPLY_DISCOUNT). Not together with line discounts.",
    )
    label = serializers.CharField(required=False, allow_blank=True, max_length=60)
    notes = serializers.CharField(required=False, allow_blank=True, max_length=1000)


class CartItemAddSerializer(_StrictMixin, serializers.Serializer):
    product_id = serializers.UUIDField()
    variant_id = serializers.UUIDField(required=False, allow_null=True)
    quantity = serializers.IntegerField(min_value=1, max_value=MAX_QUANTITY, default=1)
    unit_name = serializers.CharField(
        required=False, allow_blank=True, max_length=30,
        help_text="e.g. 'carton'. Left out = the product's base unit.",
    )
    discount = serializers.DecimalField(
        required=False, min_value=Decimal("0"), **MONEY, help_text="Line discount (needs APPLY_DISCOUNT)."
    )
    unit_price = serializers.DecimalField(
        required=False, allow_null=True, min_value=Decimal("0"), **MONEY,
        help_text="Price override (needs EDIT_SELLING_PRICE). null = the product's current price.",
    )


class CartItemUpdateSerializer(_StrictMixin, serializers.Serializer):
    quantity = serializers.IntegerField(
        required=False, min_value=0, max_value=MAX_QUANTITY, help_text="0 removes the line."
    )
    discount = serializers.DecimalField(required=False, min_value=Decimal("0"), **MONEY)
    unit_price = serializers.DecimalField(required=False, allow_null=True, min_value=Decimal("0"), **MONEY)


class CartHoldSerializer(_StrictMixin, serializers.Serializer):
    label = serializers.CharField(required=False, allow_blank=True, max_length=60)


class CartPaymentSerializer(_StrictMixin, serializers.Serializer):
    method = PaymentMethodField()
    amount = serializers.DecimalField(
        required=False, allow_null=True, min_value=Decimal("0"), **MONEY,
        help_text="Amount tendered. Left out = the full total. Cash above the total returns `change_due`.",
    )
    reference = serializers.CharField(required=False, allow_blank=True, max_length=100, default="")


class CartCheckoutSerializer(_StrictMixin, serializers.Serializer):
    payment = CartPaymentSerializer(
        required=False, allow_null=True, help_text="Money taken now. Leave out to sell on credit."
    )
    due_date = serializers.DateField(required=False, allow_null=True, help_text="For credit sales.")


# --------------------------------------------------------------------------- #
# Responses (built from services.carts.PricedCart)
# --------------------------------------------------------------------------- #
class CartLineSerializer(serializers.Serializer):
    id = serializers.CharField(source="item.pk")
    product = serializers.SerializerMethodField()
    variant = serializers.SerializerMethodField()
    unit_name = serializers.CharField(source="item.unit_name")
    quantity = serializers.IntegerField(source="item.quantity")
    unit_price = serializers.DecimalField(**MONEY)
    catalog_price = serializers.DecimalField(allow_null=True, **MONEY)
    price_overridden = serializers.BooleanField()
    subtotal = serializers.DecimalField(**MONEY)
    discount = serializers.DecimalField(**MONEY)
    tax = serializers.DecimalField(**MONEY)
    tax_rate = serializers.DecimalField(max_digits=7, decimal_places=4)
    total = serializers.DecimalField(**MONEY)
    available_quantity = serializers.IntegerField(
        allow_null=True, help_text="In stock at the cart's location, in this line's unit. null = not tracked."
    )
    in_stock = serializers.BooleanField()

    def get_product(self, line) -> dict:
        product = line.item.product
        return {"id": str(product.pk), "name": product.name, "sku": product.sku}

    def get_variant(self, line) -> dict | None:
        variant = line.item.variant
        if variant is None:
            return None
        return {"id": str(variant.pk), "name": str(variant), "sku": getattr(variant, "sku", None)}


class CartIssueSerializer(serializers.Serializer):
    code = serializers.CharField()
    message = serializers.CharField()
    item_id = serializers.CharField(required=False)


class CartSerializer(serializers.Serializer):
    id = serializers.CharField(source="cart.pk")
    status = serializers.ChoiceField(source="cart.status", choices=CartStatus.choices)
    version = serializers.IntegerField(source="cart.version")
    label = serializers.CharField(source="cart.label")
    notes = serializers.CharField(source="cart.notes")
    location = serializers.SerializerMethodField()
    customer = serializers.SerializerMethodField()
    lines = CartLineSerializer(many=True)
    item_count = serializers.IntegerField()
    subtotal = serializers.DecimalField(**MONEY)
    discount_amount = serializers.DecimalField(**MONEY)
    cart_discount = serializers.DecimalField(source="cart.discount_amount", **MONEY)
    tax_amount = serializers.DecimalField(**MONEY)
    total = serializers.DecimalField(**MONEY)
    prices_include_tax = serializers.BooleanField()
    checkout_ready = serializers.BooleanField()
    issues = CartIssueSerializer(many=True)
    held_at = serializers.DateTimeField(source="cart.held_at", allow_null=True)
    updated_at = serializers.DateTimeField(source="cart.last_activity_at")

    def get_location(self, priced) -> dict:
        warehouse = priced.cart.warehouse
        return {"id": str(warehouse.pk), "name": warehouse.name}

    def get_customer(self, priced) -> dict | None:
        customer = priced.cart.customer
        if customer is None:
            return None
        return {"id": str(customer.pk), "name": customer.name, "is_walk_in": customer.is_walk_in}


class CartSummarySerializer(serializers.Serializer):
    """A row in the cart list: enough to show held carts without pricing each one."""

    id = serializers.CharField(source="pk")
    status = serializers.CharField()
    label = serializers.CharField()
    location = serializers.SerializerMethodField()
    customer = serializers.SerializerMethodField()
    line_count = serializers.IntegerField()
    item_count = serializers.IntegerField()
    held_at = serializers.DateTimeField(allow_null=True)
    updated_at = serializers.DateTimeField(source="last_activity_at")

    def get_location(self, cart) -> dict:
        return {"id": str(cart.warehouse.pk), "name": cart.warehouse.name}

    def get_customer(self, cart) -> dict | None:
        if cart.customer is None:
            return None
        return {"id": str(cart.customer.pk), "name": cart.customer.name, "is_walk_in": cart.customer.is_walk_in}
