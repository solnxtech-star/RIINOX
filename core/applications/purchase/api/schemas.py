from drf_spectacular.utils import extend_schema, extend_schema_view, OpenApiResponse
from core.applications.purchase.api.serializers import PurchaseReceiveSerializer

purchase_schema = extend_schema_view(
    list=extend_schema(tags=['Purchases']),
    create=extend_schema(tags=['Purchases']),
    retrieve=extend_schema(tags=['Purchases']),
    update=extend_schema(tags=['Purchases']),
    partial_update=extend_schema(tags=['Purchases']),
    destroy=extend_schema(tags=['Purchases']),
    receive=extend_schema(
        tags=['Purchases'],
        request=PurchaseReceiveSerializer,
        responses={200: OpenApiResponse(description="Purchase Received and Inventory Updated")}
    )
)
