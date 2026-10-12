import logging
import traceback
from typing import Any
from typing import NoReturn

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db.models import ProtectedError
from django.db.models import RestrictedError
from django.http import Http404
from drf_spectacular.utils import OpenApiExample
from drf_spectacular.utils import OpenApiResponse
from rest_framework import exceptions
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler
from rest_framework.views import set_rollback

logger = logging.getLogger(__name__)


# ===========================================================================
# Exception types
# ===========================================================================
class CustomError:
    """Namespace of project-wide API exceptions plus helpers to document them."""

    class EmailSendError(exceptions.APIException):
        status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        default_detail = "Email could not be sent."
        default_code = "email_error"

    class Forbidden(exceptions.APIException):
        status_code = status.HTTP_403_FORBIDDEN
        default_detail = "Forbidden."
        default_code = "forbidden"

    class ServiceUnavailable(exceptions.APIException):
        status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        default_detail = "Service unavailable."
        default_code = "service_unavailable"

    class BadRequest(exceptions.APIException):
        status_code = status.HTTP_400_BAD_REQUEST
        default_detail = "Bad request."
        default_code = "bad_request"

    class EmptyResponse(exceptions.APIException):
        """Not an error: raising it returns ``200`` with an empty list.

        Prefer returning ``Response([])`` directly. Kept for backwards
        compatibility and deliberately left out of the documented errors.
        """

        status_code = status.HTTP_200_OK
        default_detail = []
        default_code = "empty_response"

    class NotFound(exceptions.APIException):
        status_code = status.HTTP_404_NOT_FOUND
        default_detail = "Not found."
        default_code = "not_found"

    class NotAcceptable(exceptions.APIException):
        status_code = status.HTTP_406_NOT_ACCEPTABLE
        default_detail = "Not acceptable."
        default_code = "not_acceptable"

    class MethodNotAllowed(exceptions.APIException):
        status_code = status.HTTP_405_METHOD_NOT_ALLOWED
        default_detail = "Method not allowed."
        default_code = "method_not_allowed"

    class Redirect(exceptions.APIException):
        """Not an error response: excluded from the documented errors."""

        status_code = status.HTTP_307_TEMPORARY_REDIRECT
        default_detail = "Temporary redirect."
        default_code = "temporary_redirect"

    class UnAuthorized(exceptions.APIException):
        status_code = status.HTTP_401_UNAUTHORIZED
        default_detail = "Authentication credentials were not provided."
        default_code = "unauthorized"

    # Exceptions documented on endpoints. ``EmptyResponse`` (200) and
    # ``Redirect`` (307) are intentionally absent: they are not errors, and a
    # ``200`` entry here would overwrite the endpoint's real success response.
    error_responses = (
        "Forbidden",
        "ServiceUnavailable",
        "BadRequest",
        "NotFound",
        "NotAcceptable",
        "MethodNotAllowed",
        "UnAuthorized",
    )

    @classmethod
    def raise_error(cls, message: str, exception: str = "BadRequest") -> NoReturn:
        """Raise one of the exceptions above by name, with a custom message."""
        exc_class = getattr(cls, exception, None)
        if not (isinstance(exc_class, type) and issubclass(exc_class, exceptions.APIException)):
            msg = f"Unknown CustomError type: {exception!r}"
            raise ValueError(msg)
        raise exc_class(message)

    @classmethod
    def DEFAULT_ERROR_SCHEMA(cls) -> dict[int, dict[str, Any]]:  # noqa: N802
        """Documented error responses, keyed by HTTP status code."""
        return {
            exc_class.status_code: _error_body_schema(exc_class)
            for exc_class in (getattr(cls, name) for name in cls.error_responses)
        }


class BusinessRuleError(Exception):
    """A violated business rule.

    Services raise it; they know nothing about HTTP beyond the suggested status
    code. ``code`` is the stable, machine-readable contract the frontend
    switches on (for example ``USER_LIMIT_REACHED``).
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = status.HTTP_400_BAD_REQUEST,
        extra: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.extra = extra or {}


# ===========================================================================
# OpenAPI helpers
# ===========================================================================
def _error_body_schema(exc_class: type[exceptions.APIException]) -> dict[str, Any]:
    """OpenAPI schema for the ``{"code", "detail"}`` error body of one exception."""
    return {
        "type": "object",
        "properties": {
            "code": {"type": "string", "example": str(exc_class.default_code).upper()},
            "detail": {"type": "string", "example": str(exc_class.default_detail)},
        },
        "required": ["code", "detail"],
    }


def create_response_schema(entity_name: str, serializer_retrieve, id_message: str) -> dict:
    """Responses for a create endpoint. Explicit entries win over the generic errors."""
    return {
        **CustomError.DEFAULT_ERROR_SCHEMA(),
        status.HTTP_201_CREATED: OpenApiResponse(
            response=serializer_retrieve,
            description=f"Successfully created {entity_name}.",
        ),
        status.HTTP_400_BAD_REQUEST: OpenApiResponse(
            response=_error_body_schema(CustomError.BadRequest),
            description="Bad request.",
            examples=[
                OpenApiExample(
                    "Already exists",
                    value={
                        "code": "BAD_REQUEST",
                        "detail": f"{entity_name.capitalize()} with the provided {id_message} already exists.",
                    },
                    response_only=True,
                ),
            ],
        ),
    }


def get_all_schema(entity_name: str, serializer_class) -> dict:
    """Responses for a list endpoint. The success response is never overwritten."""
    return {
        **CustomError.DEFAULT_ERROR_SCHEMA(),
        status.HTTP_200_OK: OpenApiResponse(
            response=serializer_class,
            description=f"Successfully retrieved the list of {entity_name}.",
        ),
    }


# ===========================================================================
# DRF exception handler (settings.REST_FRAMEWORK["EXCEPTION_HANDLER"])
# ===========================================================================
def _error_code(exc: exceptions.APIException) -> str:
    """Stable upper-case code for an APIException (``NOT_FOUND``, ``THROTTLED``...)."""
    codes = exc.get_codes()
    # ``get_codes()`` returns a dict or list when the detail is structured.
    if not isinstance(codes, str):
        codes = exc.default_code
    return str(codes).upper()


def _unexpected_error_response(exc: Exception, context: dict) -> Response:
    """Log an unhandled exception and answer with the standard 500 body.

    The traceback is always logged. It is added to the response body only when
    ``DEBUG`` is on, so it is never exposed in production.
    """
    view = context.get("view")
    logger.error(
        "Unhandled exception in %s",
        view.__class__.__name__ if view is not None else "unknown view",
        exc_info=exc,
    )
    # The handler swallows the exception, so Django will not roll back an
    # ATOMIC_REQUESTS transaction by itself.
    set_rollback()

    body: dict[str, Any] = {
        "code": "INTERNAL_SERVER_ERROR",
        "detail": "An unexpected error occurred.",
    }
    if settings.DEBUG:
        body["detail"] = str(exc) or exc.__class__.__name__
        body["traceback"] = "".join(traceback.format_exception(exc)).splitlines()
    return Response(body, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


def api_exception_handler(exc: Exception, context: dict) -> Response:
    """One error shape for everything: ``{"code", "detail", ...}``."""
    if isinstance(exc, BusinessRuleError):
        set_rollback()
        return Response(
            # ``extra`` goes first so it can never override ``code`` or ``detail``.
            {**exc.extra, "code": exc.code, "detail": exc.message},
            status=exc.status_code,
        )

    if isinstance(exc, ProtectedError | RestrictedError):
        set_rollback()
        return Response(
            {
                "code": "PROTECTED_ERROR",
                "detail": "Cannot delete this record because it is referenced by other existing records.",
            },
            status=status.HTTP_409_CONFLICT,
        )

    # Convert Django's exceptions the same way DRF does, so they also get a ``code``.
    if isinstance(exc, Http404):
        exc = exceptions.NotFound(*exc.args)
    elif isinstance(exc, PermissionDenied):
        exc = exceptions.PermissionDenied(*exc.args)

    response = drf_exception_handler(exc, context)
    if response is None:
        return _unexpected_error_response(exc, context)

    if isinstance(exc, exceptions.ValidationError):
        response.data = {
            "code": "VALIDATION_ERROR",
            "detail": "Invalid input.",
            "errors": response.data,
        }
    elif isinstance(exc, exceptions.APIException) and isinstance(response.data, dict) and "detail" in response.data:
        response.data = {"code": _error_code(exc), "detail": response.data["detail"]}
    return response
