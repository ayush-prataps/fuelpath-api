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


METERS_PER_MILE: float = 1_609.344

# Default corridor half-width: 10 miles on each side of the route (≈ 16,093.4 m).
CORRIDOR_DEFAULT_MILES: float = 10


def match_stations_to_route(
    route_coords: list[tuple[float, float]],   # (lon, lat) sequence
    stations: list,                             # list[FuelStation ORM objects]
    corridor_miles: float = CORRIDOR_DEFAULT_MILES,  # miles; default 10 mi (≈ 16,093.4 m)
) -> list:
    """
    Project each station onto the route polyline and filter by corridor.

    Returns a list of apps.optimizer.types.Station dataclasses sorted by
    ``distance_along_route_m``.

    TODO: implement using scipy.spatial.KDTree or shapely nearest_points.
    """
    corridor_m = corridor_miles * METERS_PER_MILE  # distance math against OSRM coordinate output
    raise NotImplementedError(
        "match_stations_to_route is not yet implemented."
    )
