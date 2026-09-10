"""
Fuel-stop optimizer solver.
===========================
Public surface:

    optimise(
        route_distance_m: float,
        stations: list[Station],      # pre-filtered to route corridor,
                                      # sorted by distance_along_route_m
        vehicle: VehicleConfig,
    ) -> OptimizationResult

Algorithm sketch (to be implemented):
--------------------------------------
1.  Build a DAG where:
      - Node 0      = trip start (distance = 0, price = 0)
      - Nodes 1…N   = candidate fuel stations (sorted by distance along route)
      - Node N+1    = trip end  (distance = route_distance_m)
    An edge (i → j) exists iff station j is reachable from i without
    exceeding the vehicle range.

2.  Run Dijkstra / DP over the DAG minimising total fuel cost.
      - Edge weight = cost of fuel purchased at node i to reach node j.
      - Assume tank is topped up to exactly the amount needed to reach j
        (or full, if the leg is longer than half the range — a tunable
        strategy).

3.  Reconstruct the optimal path → sequence of FuelStop objects.

4.  Verify feasibility: if no path exists (gap > range), raise
    InfeasibleRouteError with the offending gap distance.

This module has zero Django imports; inject settings from the call-site.
"""

from __future__ import annotations

import logging

from .types import FuelStop, OptimizationResult, Station, VehicleConfig

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class InfeasibleRouteError(Exception):
    """
    Raised when no path through the station graph covers the full route
    within the vehicle's range (e.g., a gap > 500 miles with no stations).
    """

    def __init__(self, gap_start_m: float, gap_end_m: float) -> None:
        self.gap_start_m = gap_start_m
        self.gap_end_m = gap_end_m
        super().__init__(
            f"No fuel station found between {gap_start_m / 1609:.1f} mi "
            f"and {gap_end_m / 1609:.1f} mi along the route."
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def optimise(
    route_distance_m: float,
    stations: list[Station],
    vehicle: VehicleConfig,
) -> OptimizationResult:
    """
    Solve the cost-optimal fuel-stop sequence for a route.

    Parameters
    ----------
    route_distance_m:
        Total driving distance of the route in metres.
    stations:
        Candidate fuel stations, each with ``distance_along_route_m``
        populated, sorted ascending by that field.
    vehicle:
        Vehicle operating parameters.

    Returns
    -------
    OptimizationResult
        Contains the selected stops, quantities, and summary costs.

    Raises
    ------
    InfeasibleRouteError
        If the route cannot be completed within the vehicle range given
        the available stations.
    ValueError
        If ``stations`` is empty and the route requires refuelling.
    """
    logger.info(
        "Optimising route: %.1f mi, %d candidate stations, range %.0f mi",
        route_distance_m / 1609.344,
        len(stations),
        vehicle.range_miles,
    )

    # TODO: implement DAG construction + Dijkstra/DP
    raise NotImplementedError(
        "optimise() is not yet implemented. "
        "See module docstring for the algorithm specification."
    )
