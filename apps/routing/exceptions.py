"""
apps.routing.exceptions
=======================
Structured, typed exception hierarchy for routing and geocoding services.

No raw network, HTTP, or library exceptions escape past the routing/geocoding
module boundaries. All exceptions carry structured metadata (`code`, `message`,
`status_code`) to enable clean HTTP error mapping in the API layer.
"""

from __future__ import annotations


class RoutingServiceError(Exception):
    """Base exception for all routing service errors."""

    code: str = "routing_error"
    status_code: int = 500

    def __init__(self, message: str, *, code: str | None = None, status_code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code


class NoRouteFoundError(RoutingServiceError):
    """Raised when OSRM cannot find a driving route connecting the points."""

    code = "no_route_found"
    status_code = 404


class RoutingTimeoutError(RoutingServiceError):
    """Raised when the OSRM routing HTTP request times out."""

    code = "routing_timeout"
    status_code = 504


class RoutingUnreachableError(RoutingServiceError):
    """Raised when the OSRM routing service is unreachable or returns 5xx."""

    code = "routing_unreachable"
    status_code = 502


class GeocodingError(RoutingServiceError):
    """Base exception for geocoding failures."""

    code = "geocoding_error"
    status_code = 502


class LocationNotFoundError(GeocodingError):
    """Raised when a location string cannot be resolved to coordinates."""

    code = "location_not_found"
    status_code = 400


class LocationOutsideUSError(GeocodingError):
    """Raised when a query or coordinate falls outside the contiguous US."""

    code = "location_outside_us"
    status_code = 400
