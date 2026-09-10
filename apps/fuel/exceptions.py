"""
Custom DRF exception handler.

Wraps DRF's default handler to produce a consistent error envelope:

    {
        "error": {
            "code":    "validation_error" | "not_found" | "server_error" | ...,
            "message": "Human-readable summary.",
            "detail":  <original DRF detail payload>
        }
    }
"""

import logging

from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

logger = logging.getLogger(__name__)


def custom_exception_handler(exc: Exception, context: dict) -> Response | None:
    response = drf_exception_handler(exc, context)

    if response is None:
        # Unhandled exception — let Django's 500 machinery take over.
        logger.exception("Unhandled exception in view %s", context.get("view"))
        return None

    # Map HTTP status to a terse code string.
    status_code: int = response.status_code
    code_map = {
        400: "bad_request",
        401: "unauthorized",
        403: "forbidden",
        404: "not_found",
        405: "method_not_allowed",
        429: "rate_limited",
        500: "server_error",
        501: "not_implemented",
    }
    code = code_map.get(status_code, f"http_{status_code}")

    # DRF validation errors carry a 'detail' field.
    if hasattr(exc, "detail"):
        message = str(exc.detail) if isinstance(exc.detail, str) else "Request error."
        detail = exc.detail
    else:
        message = str(exc)
        detail = None

    response.data = {
        "error": {
            "code": code,
            "message": message,
            "detail": detail,
        }
    }
    return response
