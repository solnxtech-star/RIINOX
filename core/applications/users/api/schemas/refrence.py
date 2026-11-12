from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample
from drf_spectacular.utils import OpenApiParameter
from drf_spectacular.utils import OpenApiResponse
from drf_spectacular.utils import extend_schema
from drf_spectacular.utils import extend_schema_view

from core.applications.users.api.serializers import (
    organization_serializers as org_serializers,
)

from .common import NOT_FOUND
from .common import UNAUTHENTICATED
from .common import cache_header
from .common import error_examples

# ===========================================================================
# Business types   (router prefix: /business-type/)
# ===========================================================================
business_type_schema = extend_schema_view(
    list=extend_schema(
        summary="List business types",
        description=(
            "The catalog of business types, ordered for display.\n\n"
            "- `?context=onboarding` returns only the cards shown on the onboarding screen.\n"
            "- Without it you get every active type (use that for the later \"edit business types\" screen).\n\n"
            "`code` is the stable key every other endpoint uses (for example `retail`, `wholesale_distribution`). "
            "Never hard-code names or order: render what the API returns."
        ),
        parameters=[
            OpenApiParameter(
                "context",
                OpenApiTypes.STR,
                OpenApiParameter.QUERY,
                enum=["onboarding"],
                description="`onboarding` returns only the cards shown on the onboarding screen.",
            )
        ],
        responses={
            200: OpenApiResponse(
                response=org_serializers.BusinessTypeSerializer(many=True),
                description="Business types.",
            ),
            401: UNAUTHENTICATED,
        },
    ),
    retrieve=extend_schema(
        summary="Retrieve a business type",
        description="One business type by its `code` (for example `wholesale_distribution`).",
        responses={
            200: OpenApiResponse(response=org_serializers.BusinessTypeSerializer, description="The business type."),
            401: UNAUTHENTICATED,
            404: NOT_FOUND,
        },
    ),
)


# ===========================================================================
# Metadata   (router prefix: /meta-data/)
# ===========================================================================
metadata_schema = extend_schema_view(
    countries=extend_schema(
        summary="List countries",
        description=(
            "Every ISO 3166-1 country, sorted by name. `code` is the two-letter code the other endpoints accept "
            "(for example `country` when creating an organization). Static data: safe to cache."
        ),
        parameters=[cache_header("private, max-age=86400")],
        responses={
            200: OpenApiResponse(
                response=org_serializers.OptionSerializer(many=True),
                description="Countries.",
                examples=[
                    OpenApiExample(
                        "Countries",
                        value=[{"code": "GH", "name": "Ghana"}, {"code": "NG", "name": "Nigeria"}],
                        response_only=True,
                    )
                ],
            ),
            401: UNAUTHENTICATED,
        },
    ),
    currencies=extend_schema(
        summary="List currencies",
        description=(
            "ISO 4217 currencies, sorted by name, for the **Base currency** dropdown. Precious metals, testing "
            "codes and supranational units are excluded. Static data: safe to cache."
        ),
        parameters=[cache_header("private, max-age=86400")],
        responses={
            200: OpenApiResponse(
                response=org_serializers.OptionSerializer(many=True),
                description="Currencies.",
                examples=[
                    OpenApiExample(
                        "Currencies",
                        value=[{"code": "GHS", "name": "Ghana Cedi"}, {"code": "NGN", "name": "Naira"}],
                        response_only=True,
                    )
                ],
            ),
            401: UNAUTHENTICATED,
        },
    ),
    states=extend_schema(
        summary="List states or regions of a country",
        description=(
            "The states, provinces or regions of one country, for the **State** dropdown. Reload it whenever the "
            "selected country changes.\n\n"
            "A valid country whose states haven't been added yet returns an **empty list**, not an error. Show a "
            "free-text field in that case: the API accepts any state text for such countries."
        ),
        parameters=[org_serializers.StatesQuerySerializer, cache_header("private, max-age=3600")],
        responses={
            200: OpenApiResponse(
                response=org_serializers.StateOptionSerializer(many=True),
                description="States of the country (possibly empty).",
                examples=[
                    OpenApiExample(
                        "Nigeria",
                        value=[{"name": "Abia", "code": "NG-AB"}, {"name": "Adamawa", "code": "NG-AD"}],
                        response_only=True,
                    )
                ],
            ),
            400: error_examples(
                "Missing or unknown country code.",
                ("Unknown country", {"country": ["Unknown country code."]}),
            ),
            401: UNAUTHENTICATED,
        },
    ),
)
