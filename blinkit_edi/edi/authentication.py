"""
Blinkit EDI - Authentication

Blinkit uses API key auth in headers. Two directions:
- Inbound:  Blinkit sends Api-Key header → we validate
- Outbound: We send Api-Key header → Blinkit validates
"""
import logging
from django.conf import settings
from rest_framework import authentication, exceptions, permissions

logger = logging.getLogger("blinkit_edi")


class BlinkitAPIKeyAuthentication(authentication.BaseAuthentication):
    """
    Validates the Api-Key header on inbound webhooks from Blinkit.
    """

    HEADER_NAME = "Api-Key"  # As per Blinkit docs
    HEADER_NAME_ALT = "HTTP_API_KEY"  # Django transforms headers

    def authenticate(self, request):
        api_key = (
            request.META.get("HTTP_API_KEY")
            or request.META.get("HTTP_API_KEY")
            or request.headers.get("Api-Key")
        )

        if not api_key:
            raise exceptions.AuthenticationFailed(
                "Missing Api-Key header"
            )

        expected_key = settings.BLINKIT_EDI.get("INBOUND_API_KEY", "")
        if not expected_key:
            logger.error("INBOUND_API_KEY not configured in settings!")
            raise exceptions.AuthenticationFailed("Server configuration error")

        if api_key != expected_key:
            logger.warning(f"Invalid API key received: {api_key[:8]}...")
            raise exceptions.AuthenticationFailed("Invalid API key")

        # Return (user, auth) tuple — no user model needed
        return (None, api_key)


class IsBlinkitAuthenticated(permissions.BasePermission):
    """
    Permission class that ensures the request has valid Blinkit API key.
    """

    def has_permission(self, request, view):
        return request.auth is not None


def get_outbound_headers():
    """Headers to send when calling Blinkit's APIs."""
    return {
        "Content-Type": settings.BLINKIT_EDI["CONTENT_TYPE"],
        "Api-Key": settings.BLINKIT_EDI["OUTBOUND_API_KEY"],
    }


def get_blinkit_endpoint(endpoint_key: str) -> str:
    """Get the correct Blinkit endpoint based on environment."""
    cfg = settings.BLINKIT_EDI
    if cfg["USE_PROD"]:
        return cfg[f"{endpoint_key}_PROD"]
    return cfg[f"{endpoint_key}_PREPROD"]
