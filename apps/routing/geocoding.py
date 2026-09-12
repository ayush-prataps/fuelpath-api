"""
apps.routing.geocoding
======================
Shared geocoding helper using Nominatim and multi-tiered caching.

Features:
- Single-location geocoding for live requests with ZERO artificial delay.
- Batch geocoding for offline ingestion with configurable rate-limiting (e.g. 1.1s).
- Multi-tier caching: Django cache framework (`LocMemCache`/Redis) + disk JSON cache.
- US bounding-box and country-code validation (contiguous US / CONUS only).
- Structured error handling: no raw geopy or network exceptions escape.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.cache import cache as django_cache
from geopy.geocoders import Nominatim

from apps.routing.exceptions import (
    GeocodingError,
    LocationNotFoundError,
    LocationOutsideUSError,
)

logger = logging.getLogger(__name__)

# Contiguous United States (CONUS) bounding box:
# Lat: 24.0°N to 50.0°N, Lon: -125.0°W to -66.0°W
CONUS_MIN_LAT = 24.0
CONUS_MAX_LAT = 50.0
CONUS_MIN_LON = -125.0
CONUS_MAX_LON = -66.0

DEFAULT_CACHE_TTL = 60 * 60 * 24 * 7  # 7 days


def is_in_conus(latitude: float, longitude: float) -> bool:
    """Return True if (lat, lon) falls within the contiguous US bounding box."""
    return (
        CONUS_MIN_LAT <= latitude <= CONUS_MAX_LAT
        and CONUS_MIN_LON <= longitude <= CONUS_MAX_LON
    )


def load_geocache_file(path: Path) -> dict[str, dict | None]:
    """Load on-disk JSON geocode cache. Returns {} if not found."""
    if path.exists():
        with path.open(encoding="utf-8") as fh:
            return json.load(fh)
    return {}


def save_geocache_file(path: Path, data: dict) -> None:
    """Save geocache dictionary to disk as formatted JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)


class NominatimGeocoder:
    """
    Reusable Nominatim geocoder with caching and validation.
    """

    def __init__(
        self,
        *,
        user_agent: str | None = None,
        cache_path: Path | str | None = None,
        geolocator: Any = None,
        cache_backend: Any = None,
    ) -> None:
        self.user_agent = user_agent or getattr(
            settings, "GEOCODER_USER_AGENT", "fuelpath-routing/1.0"
        )
        self.geolocator = geolocator or Nominatim(user_agent=self.user_agent)
        self.cache_path = Path(cache_path) if cache_path else None
        self.cache_backend = cache_backend or django_cache
        self._disk_cache: dict[str, dict | None] | None = None

    def _get_disk_cache(self) -> dict[str, dict | None]:
        if self._disk_cache is None:
            if self.cache_path and self.cache_path.exists():
                self._disk_cache = load_geocache_file(self.cache_path)
            else:
                self._disk_cache = {}
        return self._disk_cache

    def geocode(
        self,
        query: str,
        *,
        delay: float = 0.0,
        validate_conus: bool = True,
    ) -> tuple[float, float]:
        """
        Geocode a location query string to (latitude, longitude).

        - Checks Django cache first, then on-disk JSON cache if configured.
        - On miss, queries Nominatim.
        - Default delay=0.0 adds NO artificial sleep to live API requests.
        - Raises LocationNotFoundError if unresolvable.
        - Raises LocationOutsideUSError if outside contiguous US.
        - Raises GeocodingError on network/service failure.
        """
        clean_query = query.strip()
        if not clean_query:
            raise LocationNotFoundError("Location query cannot be empty.")

        safe_query = clean_query.lower().replace(" ", "_").replace(",", "_")
        cache_key = f"geocode:{safe_query}"

        # 1. Check Django cache
        cached = self.cache_backend.get(cache_key)
        if cached is not None:
            if cached is False:
                raise LocationNotFoundError(f"Could not resolve location: '{clean_query}'")
            lat, lon = cached["latitude"], cached["longitude"]
            if validate_conus and not is_in_conus(lat, lon):
                raise LocationOutsideUSError(
                    f"Location '{clean_query}' ({lat}, {lon}) is outside the contiguous US."
                )
            return lat, lon

        # 2. Check disk cache if available
        disk_cache = self._get_disk_cache()
        if clean_query in disk_cache:
            coords = disk_cache[clean_query]
            if coords is None:
                self.cache_backend.set(cache_key, False, timeout=DEFAULT_CACHE_TTL)
                raise LocationNotFoundError(f"Could not resolve location: '{clean_query}'")
            lat, lon = coords["latitude"], coords["longitude"]
            self.cache_backend.set(cache_key, coords, timeout=DEFAULT_CACHE_TTL)
            if validate_conus and not is_in_conus(lat, lon):
                raise LocationOutsideUSError(
                    f"Location '{clean_query}' ({lat}, {lon}) is outside the contiguous US."
                )
            return lat, lon

        # 3. Call Nominatim
        try:
            location = self.geolocator.geocode(clean_query, timeout=10, addressdetails=True)
        except Exception as exc:
            logger.warning("Nominatim error for query %r: %s", clean_query, exc)
            raise GeocodingError(f"Geocoding service error for '{clean_query}': {exc}") from exc

        if delay > 0:
            time.sleep(delay)

        if location is None:
            # Negative caching
            self.cache_backend.set(cache_key, False, timeout=DEFAULT_CACHE_TTL)
            if self.cache_path:
                disk_cache[clean_query] = None
                save_geocache_file(self.cache_path, disk_cache)
            raise LocationNotFoundError(f"Could not resolve location: '{clean_query}'")

        lat = float(location.latitude)
        lon = float(location.longitude)

        # 4. Check country code if present in address details
        raw_address = getattr(location, "raw", {}).get("address", {})
        country_code = raw_address.get("country_code", "").lower()
        if country_code and country_code not in ("us", "usa"):
            raise LocationOutsideUSError(
                f"Location '{clean_query}' resolved to '{country_code.upper()}', outside the United States."
            )

        # 5. Check contiguous US bounding box
        if validate_conus and not is_in_conus(lat, lon):
            raise LocationOutsideUSError(
                f"Location '{clean_query}' ({lat:.4f}, {lon:.4f}) is outside the contiguous US."
            )

        # 6. Save to cache
        payload = {"latitude": lat, "longitude": lon}
        self.cache_backend.set(cache_key, payload, timeout=DEFAULT_CACHE_TTL)
        if self.cache_path:
            disk_cache[clean_query] = payload
            save_geocache_file(self.cache_path, disk_cache)

        logger.debug("Geocoded %r → (%.5f, %.5f)", clean_query, lat, lon)
        return lat, lon


def geocode_location(
    query: str,
    *,
    geocoder: NominatimGeocoder | None = None,
    delay: float = 0.0,
    validate_conus: bool = True,
) -> tuple[float, float]:
    """
    Convenience function to geocode a single location query string.
    Returns (latitude, longitude).
    """
    active_geocoder = geocoder or NominatimGeocoder()
    return active_geocoder.geocode(query, delay=delay, validate_conus=validate_conus)


def geocode_batch_pairs(
    pairs: set[tuple[str, str]],
    cache: dict[str, dict | None],
    delay: float = 1.1,
    cache_path: Path | None = None,
    flush_every: int = 100,
    geolocator: Any = None,
    stdout_write: Any = None,
) -> tuple[dict[str, dict | None], dict[str, Any]]:
    """
    Batch geocode (city, state) pairs for ingestion with explicit delay.

    Returns (updated_cache, stats_dict).
    """
    user_agent = getattr(settings, "GEOCODER_USER_AGENT", "fuelpath-ingestion/1.0")
    active_geolocator = geolocator or Nominatim(user_agent=user_agent)

    stats: dict[str, Any] = {
        "hits": 0,
        "new": 0,
        "failed": 0,
        "failed_pairs": [],
    }

    uncached = [p for p in sorted(pairs) if f"{p[0]},{p[1]}" not in cache]
    stats["hits"] = len(pairs) - len(uncached)
    total_uncached = len(uncached)

    for i, (city, state) in enumerate(uncached, start=1):
        key = f"{city},{state}"
        query = f"{city}, {state}, USA"
        try:
            location = active_geolocator.geocode(query, timeout=10)
            if location:
                cache[key] = {
                    "latitude": float(location.latitude),
                    "longitude": float(location.longitude),
                }
                stats["new"] += 1
                logger.debug("Geocoded %s → (%.5f, %.5f)", key, location.latitude, location.longitude)
            else:
                cache[key] = None
                stats["failed"] += 1
                stats["failed_pairs"].append((city, state))
                logger.warning("Nominatim returned no result for: %s", query)
        except Exception as exc:  # noqa: BLE001
            cache[key] = None
            stats["failed"] += 1
            stats["failed_pairs"].append((city, state))
            logger.warning("Geocoding error for %s: %s", query, exc)

        if cache_path and i % flush_every == 0:
            save_geocache_file(cache_path, cache)
            if stdout_write:
                stdout_write(f"  [{i}/{total_uncached}] geocoded … last: {key}")

        if delay > 0:
            time.sleep(delay)

    return cache, stats
