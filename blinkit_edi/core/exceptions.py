"""Custom DRF exception handler for consistent error responses."""
import logging
from rest_framework.views import exception_handler
from rest_framework.response import Response
from rest_framework import status

logger = logging.getLogger("blinkit_edi")


def custom_exception_handler(exc, context):
    response = exception_handler(exc, context)

    if response is not None:
        response.data = {
            "success": False,
            "message": str(exc.detail) if hasattr(exc, "detail") else str(exc),
            "status_code": response.status_code,
        }
    else:
        logger.exception(f"Unhandled exception: {exc}")
        response = Response(
            {
                "success": False,
                "message": "Internal server error",
                "status_code": 500,
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    return response
