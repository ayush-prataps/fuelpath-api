"""
Shared data-types for the optimizer module.

All types are plain Python dataclasses — zero Django or DRF imports — so
the solver can be developed and unit-tested in complete isolation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Canonical conversion factor for the whole optimizer package.
# Import this instead of repeating the literal in spatial.py / solver.py.
METERS_PER_MILE: float = 1_609.344


@dataclass(frozen=True)
class Station:
    """
    A single fuel station that is a candidate stop on the route.

    ``distance_along_route_m`` is populated by the spatial-index matcher
    (apps.optimizer.spatial) and represents how far along the OSRM route
    geometry the station lies, in metres.
    """

    id: int                            # DB primary key (for traceability)
    name: str
    city: str
    state: str                         # 2-char US state code
    latitude: float
    longitude: float
    retail_price_usd: float            # USD per gallon
    distance_along_route_m: float = 0.0  # set by spatial matcher

    @property
    def distance_along_route_miles(self) -> float:
        """Distance along the route in miles (derived from distance_along_route_m)."""
        return self.distance_along_route_m / METERS_PER_MILE


@dataclass(frozen=True)
class VehicleConfig:
    """Operating parameters of the vehicle."""

    range_miles: float    # maximum range on a full tank
    mpg: float            # fuel efficiency (miles per gallon)

    @property
    def range_m(self) -> float:
        """Vehicle range in metres."""
        return self.range_miles * METERS_PER_MILE

    @property
    def tank_gallons(self) -> float:
        """Full-tank capacity in gallons."""
        return self.range_miles / self.mpg


@dataclass
class FuelStop:
    """A stop selected by the optimizer, with quantities computed."""

    station: Station
    gallons: float         # gallons to purchase here
    cost_usd: float        # gallons × retail_price_usd


@dataclass
class OptimizationResult:
    """Output of apps.optimizer.solver.optimise()."""

    fuel_stops: list[FuelStop] = field(default_factory=list)
    total_gallons: float = 0.0
    total_fuel_cost_usd: float = 0.0

    # Metadata useful for debugging / logging
    route_distance_m: float = 0.0
    candidate_station_count: int = 0
