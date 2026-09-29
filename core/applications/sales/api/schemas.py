from drf_spectacular.utils import extend_schema_view, extend_schema
from core.applications.sales.api.serializers import (
    SaleSerializer,
    SaleCreateSerializer,
    SaleReturnSerializer,
    SaleReturnCreateSerializer,
)

sale_viewset_schema = extend_schema_view(
    list=extend_schema(
        summary="List Sales",
        tags=["Sales"],
        responses={200: SaleSerializer(many=True)}
    ),
    retrieve=extend_schema(
        summary="Retrieve Sale",
        tags=["Sales"],
        responses={200: SaleSerializer}
    ),
    create=extend_schema(
        summary="Create Sale",
        tags=["Sales"],
        request=SaleCreateSerializer,
        responses={201: SaleSerializer}
    ),
    update=extend_schema(
        summary="Update Sale",
        tags=["Sales"],
        request=SaleCreateSerializer,
        responses={200: SaleSerializer}
    ),
    partial_update=extend_schema(
        summary="Partial Update Sale",
        tags=["Sales"],
        request=SaleCreateSerializer,
        responses={200: SaleSerializer}
    ),
    destroy=extend_schema(
        summary="Delete Sale",
        tags=["Sales"],
        responses={204: None}
    ),
)

sale_return_viewset_schema = extend_schema_view(
    list=extend_schema(
        summary="List Sale Returns",
        tags=["Sale Returns"],
        responses={200: SaleReturnSerializer(many=True)}
    ),
    retrieve=extend_schema(
        summary="Retrieve Sale Return",
        tags=["Sale Returns"],
        responses={200: SaleReturnSerializer}
    ),
    create=extend_schema(
        summary="Create Sale Return",
        tags=["Sale Returns"],
        request=SaleReturnCreateSerializer,
        responses={201: SaleReturnSerializer}
    ),
    update=extend_schema(
        summary="Update Sale Return",
        tags=["Sale Returns"],
        request=SaleReturnCreateSerializer,
        responses={200: SaleReturnSerializer}
    ),
    partial_update=extend_schema(
        summary="Partial Update Sale Return",
        tags=["Sale Returns"],
        request=SaleReturnCreateSerializer,
        responses={200: SaleReturnSerializer}
    ),
    destroy=extend_schema(
        summary="Delete Sale Return",
        tags=["Sale Returns"],
        responses={204: None}
    ),
)
