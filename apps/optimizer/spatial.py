"""
apps.optimizer.spatial
======================
Spatial index helper — station-to-route matching.

Uses a scipy.spatial.KDTree over 3D Cartesian coordinates on the Earth sphere
to find candidate route vertices in O(log M) time, projecting stations onto
the nearest polyline segments to compute:
  1. Distance from station to nearest point on route polyline (corridor check).
  2. Cumulative distance along route (following polyline from start to projected point).

Performance:
- KD-Tree spatial index avoids O(stations * polyline_points) naive brute force.
- 3D Cartesian space eliminates latitude-dependent aspect ratio distortion.
- Candidate segment evaluation queries the top k=5 nearest vertices to handle
  sharp turns, switchbacks, and cloverleaf interchanges robustly.
- Zero-length segment epsilon guards prevent division-by-zero on consecutive
  duplicate coordinates.
- Co-located stations (same lat/lon) share projected distance by construction.
"""

from __future__ import annotations

import logging
import math
from typing import Any

import numpy as np
from scipy.spatial import KDTree

from apps.optimizer.types import METERS_PER_MILE, Station

logger = logging.getLogger(__name__)

# Constants
EARTH_RADIUS_MILES: float = 3_958.761  # 6,371,000 m / 1,609.344

# Default corridor half-width: 10 miles on each side of the route (≈ 16,093.4 m).
CORRIDOR_DEFAULT_MILES: float = 10


def _chord_to_arc_miles(chord_dist: float) -> float:
    """Convert Euclidean chord distance in 3D Cartesian space to great-circle arc distance."""
    sin_half = min(1.0, max(0.0, chord_dist / (2.0 * EARTH_RADIUS_MILES)))
    return 2.0 * EARTH_RADIUS_MILES * math.asin(sin_half)


def _coords_to_cartesian_3d(lats: np.ndarray, lons: np.ndarray) -> np.ndarray:
    """Vectorized conversion of (lat, lon) arrays in degrees to 3D Cartesian coordinates (miles)."""
    phi = np.radians(lats)
    lam = np.radians(lons)
    x = EARTH_RADIUS_MILES * np.cos(phi) * np.cos(lam)
    y = EARTH_RADIUS_MILES * np.cos(phi) * np.sin(lam)
    z = EARTH_RADIUS_MILES * np.sin(phi)
    return np.column_stack([x, y, z])


def _extract_station_attrs(station: Any) -> tuple[int, str, str, str, float, float, float] | None:
    """
    Extract (id, name, city, state, lat, lon, price) from FuelStation ORM object,
    Station dataclass, or dict. Returns None if lat or lon is missing.
    """
    if isinstance(station, dict):
        lat = station.get("latitude")
        lon = station.get("longitude")
        if lat is None or lon is None:
            return None
        st_id = station.get("id", 0)
        name = station.get("name", "")
        city = station.get("city", "")
        state = station.get("state", "")
        price = float(station.get("retail_price_usd", station.get("price", 0.0)))
        return int(st_id), str(name), str(city), str(state), float(lat), float(lon), price

    # Object / ORM model / dataclass
    lat = getattr(station, "latitude", None)
    lon = getattr(station, "longitude", None)
    if lat is None or lon is None:
        return None

    st_id = getattr(station, "id", 0)
    name = getattr(station, "name", "")
    city = getattr(station, "city", "")
    state = getattr(station, "state", "")
    price = float(getattr(station, "retail_price_usd", getattr(station, "price", 0.0)))
    return int(st_id), str(name), str(city), str(state), float(lat), float(lon), price


def match_stations_to_route(
    route_coords: list[tuple[float, float]] | list[list[float]] | dict[str, Any],
    stations: list,
    corridor_miles: float = CORRIDOR_DEFAULT_MILES,
) -> list[Station]:
    """
    Project candidate fuel stations onto the route polyline and filter by corridor.

    Args:
        route_coords: List of (lon, lat) points or GeoJSON FeatureCollection/Feature/geometry.
        stations: Iterable of FuelStation ORM objects, Station dataclasses, or dicts.
        corridor_miles: Maximum allowable perpendicular distance from polyline (default 10 miles).

    Returns:
        List of apps.optimizer.types.Station dataclasses sorted by distance_along_route_m ascending.
    """
    # 1. Normalize route coordinates
    if isinstance(route_coords, dict):
        if "features" in route_coords:
            raw_coords = route_coords["features"][0]["geometry"]["coordinates"]
        elif "geometry" in route_coords:
            raw_coords = route_coords["geometry"]["coordinates"]
        elif "coordinates" in route_coords:
            raw_coords = route_coords["coordinates"]
        else:
            raw_coords = []
    else:
        raw_coords = route_coords

    if not raw_coords:
        return []

    # OSRM coordinates are [lon, lat]
    route_lons = np.array([float(p[0]) for p in raw_coords], dtype=np.float64)
    route_lats = np.array([float(p[1]) for p in raw_coords], dtype=np.float64)
    m_points = len(route_lons)

    # 2. Build 3D Cartesian coordinates for route
    route_3d = _coords_to_cartesian_3d(route_lats, route_lons)

    # 3. Compute cumulative polyline distances (miles)
    cum_dist_miles = np.zeros(m_points, dtype=np.float64)
    for i in range(1, m_points):
        chord_len = float(np.linalg.norm(route_3d[i] - route_3d[i - 1]))
        seg_len_miles = _chord_to_arc_miles(chord_len)
        cum_dist_miles[i] = cum_dist_miles[i - 1] + seg_len_miles

    # 4. Build spatial index (KDTree) over route vertices
    tree = KDTree(route_3d)

    # 5. Extract and filter valid stations
    valid_stations = []
    for st in stations:
        attrs = _extract_station_attrs(st)
        if attrs is not None:
            valid_stations.append(attrs)

    if not valid_stations:
        return []

    # 6. Cache projections for co-located stations (identical coordinates)
    # Mapping: (rounded_lat, rounded_lon) -> (is_within_corridor, dist_along_route_miles)
    coord_cache: dict[tuple[float, float], tuple[bool, float]] = {}

    k_nearest = min(5, m_points)
    epsilon = 1e-12

    matched_stations: list[Station] = []

    for st_id, name, city, state, lat, lon, price in valid_stations:
        cache_key = (round(lat, 6), round(lon, 6))

        if cache_key in coord_cache:
            in_corridor, dist_along_miles = coord_cache[cache_key]
        else:
            st_3d = _coords_to_cartesian_3d(np.array([lat]), np.array([lon]))[0]

            if m_points == 1:
                # Single-point route degenerate case
                chord_dist = float(np.linalg.norm(st_3d - route_3d[0]))
                dist_miles = _chord_to_arc_miles(chord_dist)
                in_corridor = dist_miles <= corridor_miles
                dist_along_miles = 0.0
            else:
                # Query top k nearest vertices
                _, indices = tree.query(st_3d, k=k_nearest)
                if isinstance(indices, (int, np.integer)):
                    indices = [int(indices)]
                else:
                    indices = [int(idx) for idx in indices]

                # Collect unique incident segments [idx, idx+1]
                candidate_segs: set[int] = set()
                for idx in indices:
                    if idx > 0:
                        candidate_segs.add(idx - 1)
                    if idx < m_points - 1:
                        candidate_segs.add(idx)

                best_dist_miles = float("inf")
                best_along_miles = 0.0

                for seg_idx in candidate_segs:
                    p_a = route_3d[seg_idx]
                    p_b = route_3d[seg_idx + 1]
                    v = p_b - p_a
                    v_sq = float(np.dot(v, v))

                    # Zero-length segment guard against duplicate consecutive coordinates
                    if v_sq < epsilon:
                        q = p_a
                        t_clamped = 0.0
                    else:
                        t = float(np.dot(st_3d - p_a, v) / v_sq)
                        t_clamped = max(0.0, min(1.0, t))
                        q = p_a + t_clamped * v

                    chord_dist = float(np.linalg.norm(st_3d - q))
                    seg_dist_miles = _chord_to_arc_miles(chord_dist)

                    if seg_dist_miles < best_dist_miles:
                        best_dist_miles = seg_dist_miles
                        best_along_miles = (
                            cum_dist_miles[seg_idx]
                            + t_clamped * (cum_dist_miles[seg_idx + 1] - cum_dist_miles[seg_idx])
                        )

                in_corridor = best_dist_miles <= corridor_miles
                dist_along_miles = best_along_miles

            coord_cache[cache_key] = (in_corridor, dist_along_miles)

        if in_corridor:
            # Canonical storage in Station is meters; miles exposed via .distance_along_route_miles property
            dist_along_m = dist_along_miles * METERS_PER_MILE
            station_obj = Station(
                id=st_id,
                name=name,
                city=city,
                state=state,
                latitude=lat,
                longitude=lon,
                retail_price_usd=price,
                distance_along_route_m=dist_along_m,
            )
            matched_stations.append(station_obj)

    # 7. Sort ascending by distance along route
    matched_stations.sort(key=lambda s: s.distance_along_route_m)
    return matched_stations
