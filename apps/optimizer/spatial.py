"""
Spatial index helper — station-to-route matching.
==================================================
Given a route geometry (list of (lon, lat) points) and a queryset of
FuelStation objects, this module:

  1. Builds an in-memory R-tree (or scipy KD-tree) over the station set.
  2. For each station, finds the nearest point on the route polyline and
     records its distance along the route (in metres).
  3. Filters to stations within a configurable corridor width.
  4. Returns a sorted list of apps.optimizer.types.Station dataclasses,
     ready to be fed into the DP solver.

No Django ORM calls are made here — the caller fetches and passes in the
station queryset / list.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def match_stations_to_route(
    route_coords: list[tuple[float, float]],   # (lon, lat) sequence
    stations: list,                             # list[FuelStation ORM objects]
    corridor_m: float = 10_000,                # ≈ 6.2 miles
) -> list:
    """
    Project each station onto the route polyline and filter by corridor.

    Returns a list of apps.optimizer.types.Station dataclasses sorted by
    ``distance_along_route_m``.

    TODO: implement using scipy.spatial.KDTree or shapely nearest_points.
    """
    raise NotImplementedError(
        "match_stations_to_route is not yet implemented."
    )
