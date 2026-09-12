"""
apps.routing — OSRM routing client & geocoding service
======================================================
Public interface:
    - get_route: OSRM client returning mile-denominated RouteResult
    - Coordinate: (latitude, longitude) geographic point
    - RouteResult: Parsed GeoJSON FeatureCollection + route metadata
    - geocode_location: Single location geocoding helper (zero artificial delay)
    - NominatimGeocoder: Reusable Nominatim geocoding client
    - Exceptions: NoRouteFoundError, RoutingTimeoutError, RoutingUnreachableError,
                  LocationNotFoundError, LocationOutsideUSError, RoutingServiceError
"""

from apps.routing.exceptions import (
    GeocodingError,
    LocationNotFoundError,
    LocationOutsideUSError,
    NoRouteFoundError,
    RoutingServiceError,
    RoutingTimeoutError,
    RoutingUnreachableError,
)
from apps.routing.geocoding import (
    CONUS_MAX_LAT,
    CONUS_MAX_LON,
    CONUS_MIN_LAT,
    CONUS_MIN_LON,
    NominatimGeocoder,
    geocode_batch_pairs,
    geocode_location,
    is_in_conus,
)
from apps.routing.service import (
    Coordinate,
    RouteResult,
    get_route,
)

__all__ = [
    "CONUS_MAX_LAT",
    "CONUS_MAX_LON",
    "CONUS_MIN_LAT",
    "CONUS_MIN_LON",
    "Coordinate",
    "GeocodingError",
    "LocationNotFoundError",
    "LocationOutsideUSError",
    "NoRouteFoundError",
    "NominatimGeocoder",
    "RouteResult",
    "RoutingServiceError",
    "RoutingTimeoutError",
    "RoutingUnreachableError",
    "geocode_batch_pairs",
    "geocode_location",
    "get_route",
    "is_in_conus",
]
