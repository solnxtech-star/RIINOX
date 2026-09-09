from drf_spectacular.utils import OpenApiResponse
from rest_framework import status
from rest_framework.exceptions import APIException
from rest_framework.views import exception_handler


class CustomError:
    class EmailSendError(APIException):
        status_code = 500
        default_detail = "email error"
        default_code = "email_error"

    class Forbidden(APIException):
        status_code = status.HTTP_403_FORBIDDEN
        default_detail = "forbidden"
        default_code = "forbidden"

    class ServiceUnavailable(APIException):
        status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        default_detail = "Service unavailable."
        default_code = "service_unavailable"

    class BadRequest(APIException):
        status_code = status.HTTP_400_BAD_REQUEST
        default_detail = "bad request"
        default_code = "bad_request"

    class EmptyResponse(APIException):
        status_code = status.HTTP_200_OK
        default_detail = []
        default_code = status.HTTP_200_OK

    class NotFound(APIException):
        status_code = status.HTTP_404_NOT_FOUND
        default_detail = "Not Found"
        default_code = status.HTTP_404_NOT_FOUND

    class NotAcceptable(APIException):
        status_code = status.HTTP_406_NOT_ACCEPTABLE
        default_detail = "Not acceptable."
        default_code = "not_acceptable"

    class MethodNotAllowed(APIException):
        status_code = status.HTTP_405_METHOD_NOT_ALLOWED
        default_detail = "Method not allowed."
        default_code = "method_not_allowed"

    class Redirect(APIException):
        status_code = status.HTTP_307_TEMPORARY_REDIRECT
        default_detail = "Temporary Redirect."
        default_code = "temporary_redirect"

    class UnAuthorized(APIException):
        status_code = status.HTTP_401_UNAUTHORIZED
        default_detail = "Authentication credentials were not provided."
        default_code = "unauthorized"

    @classmethod
    def raise_error(
        cls,
        message: str,
        exception: str = "BadRequest",
    ):
        e: APIException = getattr(cls, exception)
        raise e(message)

    error_responses = [
        "Forbidden",
        "ServiceUnavailable",
        "BadRequest",
        "EmptyResponse",
        "NotFound",
        "NotAcceptable",
        "MethodNotAllowed",
        "Redirect",
        "UnAuthorized",
    ]

    @classmethod
    def DEFAULT_ERROR_SCHEMA(cls):  # noqa: N802
        return {
            getattr(cls, error).status_code: {
                "type": "object",
                "properties": {
                    "detail": {
                        "type": "string",
                        "example": getattr(cls, error).default_detail,
                    },
                },
            }
            for error in cls.error_responses
        }


DEFAULT_ERROR_SCHEMA = {
    status.HTTP_400_BAD_REQUEST: {
        "detail": "Bad Request",
    },
    status.HTTP_403_FORBIDDEN: {
        "detail": "You do not have the permission to perform this action",
    },
    status.HTTP_404_NOT_FOUND: {
        "detail": "Not Found",
    },
}


def create_response_schema(entity_name, serializer_retrieve, id_message):
    return {
        status.HTTP_201_CREATED: OpenApiResponse(
            response=serializer_retrieve,
            description=f"Successfully created {entity_name}.",
        ),
        status.HTTP_400_BAD_REQUEST: OpenApiResponse(
            description="Bad Request",
            response=CustomError.DEFAULT_ERROR_SCHEMA(),
            examples={
                "application/json": {
                    "detail": f"{entity_name.capitalize()} with the provided {id_message} already exists.",  # noqa: E501
                },
            },
        ),
        **CustomError.DEFAULT_ERROR_SCHEMA(),
    }


def get_all_schema(entity_name, serializer_class):
    return {
        status.HTTP_200_OK: OpenApiResponse(
            response=serializer_class,
            description="Successfully retrieved list of devices.",
        ),
        **CustomError.DEFAULT_ERROR_SCHEMA(),
    }


def custom_exception_handler(exc, context):
    # Call REST framework's default exception handler first,
    # to get the standard error response.
    response = exception_handler(exc, context)

    # Now modify the response to ensure all error values are in a list.
    if response is not None:
        if isinstance(response.data, dict):
            for key, value in response.data.items():
                if not isinstance(value, list):
                    response.data[key] = [value]
    return response
