"""
apps.routing.service
====================
Framework-light OSRM routing client with look-aside caching and error handling.

Public interface:
    get_route(start, finish) -> RouteResult
    Coordinate(latitude, longitude)
    RouteResult(geojson, distance_miles, duration_s, waypoints)

All distances are mile-denominated (`distance_miles`) — no meter/km leakage.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx
from django.conf import settings
from django.core.cache import cache as django_cache
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_fixed

from apps.routing.exceptions import (
    LocationOutsideUSError,
    NoRouteFoundError,
    RoutingServiceError,
    RoutingTimeoutError,
    RoutingUnreachableError,
)
from apps.routing.geocoding import is_in_conus

logger = logging.getLogger(__name__)

# Standard conversion factor
METERS_PER_MILE = 1_609.344


@dataclass(frozen=True)
class Coordinate:
    """A geographic point (WGS-84) in the contiguous United States."""

    latitude: float
    longitude: float

    def __str__(self) -> str:
        return f"{self.latitude:.5f},{self.longitude:.5f}"

    @property
    def lat(self) -> float:
        return self.latitude

    @property
    def lon(self) -> float:
        return self.longitude


@dataclass
class RouteResult:
    """Parsed output of a single OSRM /route call."""

    geojson: dict[str, Any]
    distance_miles: float
    duration_s: float
    waypoints: list[Coordinate] = field(default_factory=list)

    @property
    def distance_m(self) -> float:
        """Internal helper for geometry math, not the primary public field."""
        return self.distance_miles * METERS_PER_MILE

    def to_dict(self) -> dict[str, Any]:
        return {
            "geojson": self.geojson,
            "distance_miles": self.distance_miles,
            "duration_s": self.duration_s,
            "waypoints": [
                {"latitude": w.latitude, "longitude": w.longitude}
                for w in self.waypoints
            ],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RouteResult:
        waypoints = [
            Coordinate(latitude=float(w["latitude"]), longitude=float(w["longitude"]))
            for w in data.get("waypoints", [])
        ]
        return cls(
            geojson=data["geojson"],
            distance_miles=float(data["distance_miles"]),
            duration_s=float(data["duration_s"]),
            waypoints=waypoints,
        )


def _normalize_coordinate(
    coord: Coordinate | tuple[float, float] | list[float],
) -> Coordinate:
    if isinstance(coord, Coordinate):
        return coord
    if isinstance(coord, (tuple, list)) and len(coord) == 2:
        return Coordinate(latitude=float(coord[0]), longitude=float(coord[1]))
    raise ValueError(
        f"Invalid coordinate: {coord!r}. Expected Coordinate or (lat, lon) pair."
    )


def _cache_key(start: Coordinate, finish: Coordinate) -> str:
    """
    Deterministic cache key rounded to ~4 decimal places (~11m precision).
    Avoids misses caused by trivial float noise.
    """
    return (
        f"osrm:route:{start.latitude:.4f},{start.longitude:.4f}:"
        f"{finish.latitude:.4f},{finish.longitude:.4f}"
    )


# Exactly 1 retry (max 2 attempts total) on transient network/timeout errors
@retry(
    stop=stop_after_attempt(2),
    retry=retry_if_exception_type((httpx.NetworkError, httpx.TimeoutException)),
    wait=wait_fixed(0.2),
    reraise=True,
)
def _http_get_with_retry(
    client: httpx.Client, url: str, timeout: float = 10.0
) -> httpx.Response:
    return client.get(url, timeout=timeout)


def _call_osrm(
    start: Coordinate,
    finish: Coordinate,
    *,
    base_url: str | None = None,
    http_client: httpx.Client | None = None,
) -> RouteResult:
    """
    Call OSRM /route/v1/driving and parse response into RouteResult.
    """
    effective_base_url = (
        base_url
        or getattr(settings, "OSRM_BASE_URL", "https://router.project-osrm.org")
    ).rstrip("/")

    # OSRM expects coordinates in {lon},{lat} format
    coords_str = f"{start.longitude},{start.latitude};{finish.longitude},{finish.latitude}"
    url = f"{effective_base_url}/route/v1/driving/{coords_str}?overview=full&geometries=geojson"

    try:
        if http_client is not None:
            response = _http_get_with_retry(http_client, url, timeout=10.0)
        else:
            with httpx.Client() as client:
                response = _http_get_with_retry(client, url, timeout=10.0)
    except httpx.TimeoutException as exc:
        logger.warning("OSRM timeout calling %s: %s", url, exc)
        raise RoutingTimeoutError(f"OSRM route request timed out: {exc}") from exc
    except httpx.NetworkError as exc:
        logger.warning("OSRM network error calling %s: %s", url, exc)
        raise RoutingUnreachableError(
            f"OSRM routing service unreachable: {exc}"
        ) from exc
    except Exception as exc:
        logger.warning("OSRM unexpected request error: %s", exc)
        raise RoutingServiceError(f"OSRM request failed: {exc}") from exc

    if response.status_code >= 500:
        raise RoutingUnreachableError(
            f"OSRM server error ({response.status_code}): {response.text}"
        )

    try:
        data = response.json()
    except Exception as exc:
        raise RoutingServiceError(
            f"Failed to parse OSRM response as JSON: {exc}"
        ) from exc

    code = data.get("code", "")
    if response.status_code != 200 or code != "Ok":
        message = data.get("message", response.text)
        if code == "NoRoute" or "NoRoute" in message:
            raise NoRouteFoundError(
                f"No driving route found between {start} and {finish}: {message}"
            )
        if response.status_code == 404:
            raise NoRouteFoundError(f"Route not found: {message}")
        raise RoutingServiceError(f"OSRM error ({code}): {message}")

    routes = data.get("routes")
    if not routes:
        raise NoRouteFoundError(
            f"OSRM returned zero routes between {start} and {finish}."
        )

    route_data = routes[0]
    distance_m = float(route_data.get("distance", 0.0))
    distance_miles = distance_m / METERS_PER_MILE
    duration_s = float(route_data.get("duration", 0.0))
    geometry = route_data.get("geometry", {})

    geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": geometry,
                "properties": {
                    "distance_miles": distance_miles,
                    "duration_s": duration_s,
                },
            }
        ],
    }

    waypoints = [
        Coordinate(
            latitude=float(wp["location"][1]),
            longitude=float(wp["location"][0]),
        )
        for wp in data.get("waypoints", [])
        if "location" in wp and len(wp["location"]) == 2
    ]

    return RouteResult(
        geojson=geojson,
        distance_miles=distance_miles,
        duration_s=duration_s,
        waypoints=waypoints,
    )


def get_route(
    start: Coordinate | tuple[float, float] | None = None,
    finish: Coordinate | tuple[float, float] | None = None,
    *,
    origin: Coordinate | tuple[float, float] | None = None,
    destination: Coordinate | tuple[float, float] | None = None,
    base_url: str | None = None,
    cache_backend: Any = None,
    http_client: httpx.Client | None = None,
) -> RouteResult:
    """
    Retrieve route between start and finish points.

    1. Validates both coordinates are in the contiguous US (CONUS).
    2. Checks look-aside cache with rounded coordinate key (~11m precision).
    3. On miss, calls OSRM driving service via HTTP with bounded retries.
    4. Converts route distance strictly to miles (no km/meters leaked).
    5. Caches and returns RouteResult.
    """
    actual_start = start if start is not None else origin
    actual_finish = finish if finish is not None else destination

    if actual_start is None or actual_finish is None:
        raise ValueError("Both start and finish coordinates are required.")

    start_coord = _normalize_coordinate(actual_start)
    finish_coord = _normalize_coordinate(actual_finish)

    # Reject coordinates outside contiguous US early before making any HTTP call
    if not is_in_conus(start_coord.latitude, start_coord.longitude):
        raise LocationOutsideUSError(
            f"Start coordinate ({start_coord.latitude:.4f}, {start_coord.longitude:.4f}) is outside the contiguous US."
        )
    if not is_in_conus(finish_coord.latitude, finish_coord.longitude):
        raise LocationOutsideUSError(
            f"Finish coordinate ({finish_coord.latitude:.4f}, {finish_coord.longitude:.4f}) is outside the contiguous US."
        )

    cache = cache_backend or django_cache
    key = _cache_key(start_coord, finish_coord)

    cached = cache.get(key)
    if cached is not None:
        logger.debug("Route cache HIT for key %s", key)
        if isinstance(cached, str):
            return RouteResult.from_dict(json.loads(cached))
        return RouteResult.from_dict(cached)

    logger.debug("Route cache MISS — calling OSRM for key %s", key)
    result = _call_osrm(
        start_coord,
        finish_coord,
        base_url=base_url,
        http_client=http_client,
    )

    ttl: int = getattr(settings, "OSRM_CACHE_TTL", 60 * 60 * 6)
    cache.set(key, result.to_dict(), timeout=ttl)
    return result
