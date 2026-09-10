"""
OSRM routing service.
=====================
Public surface:

    get_route(origin: Coordinate, destination: Coordinate) -> RouteResult

Behaviour:
  1. Build a deterministic cache key from the coordinate pair.
  2. Check the Django cache (Redis in prod, LocMemCache in tests).
  3. On miss — call OSRM /route/v1/driving endpoint via httpx.
  4. Parse the response into a GeoJSON FeatureCollection + distance metadata.
  5. Store the result in the cache with the configured TTL.
  6. Return the RouteResult dataclass.

This module is framework-aware (reads Django settings + cache) but contains
no ORM calls; the RouteCache DB model is optional and lives in models.py.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Coordinate:
    """A geographic point (WGS-84)."""
    longitude: float
    latitude: float

    def __str__(self) -> str:
        return f"{self.longitude},{self.latitude}"


@dataclass
class RouteResult:
    """Parsed output of a single OSRM /route call."""
    geojson: dict[str, Any]          # GeoJSON FeatureCollection
    distance_m: float                 # total route distance in metres
    duration_s: float                 # estimated drive time in seconds
    waypoints: list[Coordinate] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _cache_key(origin: Coordinate, destination: Coordinate) -> str:
    """Deterministic, collision-resistant cache key for a route pair."""
    raw = f"{origin}|{destination}"
    return "osrm:" + hashlib.sha256(raw.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_route(origin: Coordinate, destination: Coordinate) -> RouteResult:
    """
    Return a RouteResult for the given origin → destination pair.

    Uses the Django cache as a look-aside cache.  On a miss the OSRM HTTP
    API is called via httpx (synchronous) and the response is cached.

    Raises:
        RoutingError  — wraps OSRM HTTP errors or parse failures.
    """
    from django.conf import settings
    from django.core.cache import cache

    key = _cache_key(origin, destination)
    cached = cache.get(key)
    if cached is not None:
        logger.debug("Route cache HIT for key %s", key)
        return RouteResult(**json.loads(cached))

    logger.debug("Route cache MISS — calling OSRM for key %s", key)
    result = _call_osrm(origin, destination, base_url=settings.OSRM_BASE_URL)

    ttl: int = getattr(settings, "OSRM_CACHE_TTL", 60 * 60 * 6)
    cache.set(key, json.dumps(result.__dict__), timeout=ttl)
    return result


def _call_osrm(
    origin: Coordinate,
    destination: Coordinate,
    *,
    base_url: str,
) -> RouteResult:
    """
    Hit the OSRM /route/v1/driving endpoint and parse the response.

    TODO: implement once the routing work begins.
    """
    raise NotImplementedError(
        "_call_osrm is not yet implemented. "
        "Wire up the httpx call and GeoJSON parsing here."
    )


class RoutingError(Exception):
    """Raised when the OSRM call fails or returns an unexpected response."""
