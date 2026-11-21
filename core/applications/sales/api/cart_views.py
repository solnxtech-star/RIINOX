from __future__ import annotations

from django.db.models import Count
from django.db.models import Sum
from django.db.models.functions import Coalesce
from rest_framework import status
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from core.applications.sales.api.cart_schemas import cart_schema
from core.applications.sales.api.cart_serializers import CartCheckoutSerializer
from core.applications.sales.api.cart_serializers import CartHoldSerializer
from core.applications.sales.api.cart_serializers import CartItemAddSerializer
from core.applications.sales.api.cart_serializers import CartItemUpdateSerializer
from core.applications.sales.api.cart_serializers import CartOpenSerializer
from core.applications.sales.api.cart_serializers import CartSerializer
from core.applications.sales.api.cart_serializers import CartSummarySerializer
from core.applications.sales.api.cart_serializers import CartUpdateSerializer
from core.applications.sales.api.serializers import SaleSerializer
from core.applications.sales.models import OPEN_CART_STATUSES
from core.applications.sales.models import Cart
from core.applications.sales.models import CartStatus
from core.applications.sales.models import Sale
from core.applications.sales.services import carts as cart_services
from core.applications.users.permissions import BaseTenantPermission
from core.applications.users.permissions import OrganizationScopedMixin
from core.helper.enums import PermissionCode
from core.helper.pagination import CustomPagination


class CartPermission(BaseTenantPermission):
    """Everything about carts is selling, so every action needs CREATE_SALE."""

    action_permissions: dict[str, str] = {}
    default_permission: str = PermissionCode.CREATE_SALE


@cart_schema
class CartViewSet(OrganizationScopedMixin, viewsets.GenericViewSet):
    """
    The caller's own carts at `/organizations/{id}/carts/`. Every change answers
    with the whole priced cart, so the client just renders the response.
    """

    permission_classes = [CartPermission]
    pagination_class = CustomPagination
    serializer_class = CartSerializer
    queryset = Cart.objects.none()
    lookup_value_regex = "[0-9a-fA-F-]{32,36}"

    # ---------------------------------------------------------------- helpers
    def _args(self):
        return self.organization, self.request.user

    def _body(self, serializer_class, *, partial: bool = False) -> dict:
        serializer = serializer_class(data=self.request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        return dict(serializer.validated_data)

    def _priced(self, cart_id, http_status=status.HTTP_200_OK) -> Response:
        cart = cart_services.get_cart(*self._args(), cart_id)
        data = CartSerializer(cart_services.price_cart(cart), context=self.get_serializer_context()).data
        return Response(data, status=http_status)

    # ------------------------------------------------------------------ carts
    def list(self, request, *args, **kwargs):
        wanted = request.query_params.get("status")
        statuses = OPEN_CART_STATUSES
        if wanted:
            if wanted not in CartStatus.values:
                raise ValidationError({"status": [f"Must be one of: {', '.join(CartStatus.values)}."]})
            statuses = (wanted,)
        queryset = (
            Cart.objects.filter(organization=self.organization, created_by=request.user, status__in=statuses)
            .select_related("warehouse", "customer")
            .annotate(line_count=Count("items"), item_count=Coalesce(Sum("items__quantity"), 0))
            .order_by("-last_activity_at")
        )
        location = request.query_params.get("location")
        if location:
            queryset = self.location_scope.narrow(queryset, location, "warehouse")
        page = self.paginate_queryset(queryset)
        return self.get_paginated_response(CartSummarySerializer(page, many=True).data)

    def create(self, request, *args, **kwargs):
        body = self._body(CartOpenSerializer)
        cart, created = cart_services.open_cart(
            *self._args(), warehouse_id=body.get("location"), scope=self.location_scope
        )
        return self._priced(cart.pk, status.HTTP_201_CREATED if created else status.HTTP_200_OK)

    def retrieve(self, request, pk=None, *args, **kwargs):
        return self._priced(pk)

    def partial_update(self, request, pk=None, *args, **kwargs):
        cart_services.update_cart(*self._args(), pk, self._body(CartUpdateSerializer))
        return self._priced(pk)

    def destroy(self, request, pk=None, *args, **kwargs):
        cart_services.discard_cart(*self._args(), pk)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["post"])
    def hold(self, request, pk=None, *args, **kwargs):
        body = self._body(CartHoldSerializer)
        cart_services.hold_cart(*self._args(), pk, label=body.get("label"))
        return self._priced(pk)

    @action(detail=True, methods=["post"])
    def resume(self, request, pk=None, *args, **kwargs):
        cart_services.resume_cart(*self._args(), pk, scope=self.location_scope)
        return self._priced(pk)

    @action(detail=True, methods=["post"])
    def clear(self, request, pk=None, *args, **kwargs):
        cart_services.clear_cart(*self._args(), pk)
        return self._priced(pk)

    @action(detail=True, methods=["post"])
    def checkout(self, request, pk=None, *args, **kwargs):
        body = self._body(CartCheckoutSerializer)
        payment = body.get("payment")
        sale, replayed = cart_services.checkout_cart(
            *self._args(),
            pk,
            payment=dict(payment) if payment else None,
            due_date=body.get("due_date"),
            scope=self.location_scope,
        )
        change_due = getattr(sale, "change_due", None)
        sale = Sale.objects.with_details().get(pk=sale.pk)
        if change_due is not None:
            sale.change_due = change_due
        data = SaleSerializer(sale, context=self.get_serializer_context()).data
        return Response(data, status=status.HTTP_200_OK if replayed else status.HTTP_201_CREATED)

    # ------------------------------------------------------------------ items
    @action(detail=True, methods=["post"], url_path="items")
    def add_item(self, request, pk=None, *args, **kwargs):
        cart_services.add_item(*self._args(), pk, self._body(CartItemAddSerializer))
        return self._priced(pk)

    @action(detail=True, methods=["patch", "delete"], url_path=r"items/(?P<item_id>[0-9a-fA-F-]{32,36})")
    def item(self, request, pk=None, item_id=None, *args, **kwargs):
        if request.method == "DELETE":
            cart_services.remove_item(*self._args(), pk, item_id)
        else:
            cart_services.update_item(*self._args(), pk, item_id, self._body(CartItemUpdateSerializer))
        return self._priced(pk)
