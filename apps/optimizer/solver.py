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

Algorithm
---------
We model the problem as a Directed Acyclic Graph (DAG) and solve it with
dynamic programming (topological order = route order, so pure O(N^2) DP):

  Node 0      -- trip start: distance = 0, price = 0 (full tank, no purchase)
  Nodes 1...N -- candidate fuel stations, sorted by distance along route
  Node N+1    -- trip end: distance = route_distance_m, price = 0

  Edge (i -> j): exists iff dist(j) - dist(i) <= range_m
  Edge weight:   cost of fuel purchased at node i to cover leg i->j exactly
                 (buy exactly enough fuel at i to reach selected downstream station j)

Pre-processing
--------------
Co-located stations (same distance_along_route_m value, within 1 m) are
collapsed to a single node at the cheapest available price at that position.
This keeps the graph small and is strictly correct.

Short-route fast path
---------------------
If route_distance_m <= vehicle.range_m the vehicle completes the journey on
its starting full tank; no stops are needed and total cost is $0.00.
This can look like a bug without the explanation -- the driver departs with
a full tank and arrives without needing to refuel.

Fuel strategy
-------------
For each candidate transition i -> j, the solver assumes the vehicle purchases
exactly enough fuel at station i to cover that selected leg. j may be any
later reachable station, not necessarily the next station in route order.
This allows the optimizer to skip expensive intermediate stations and carry
cheaper fuel forward.

This module has zero Django imports; inject settings from the call-site.
"""

from __future__ import annotations

import logging
import math
from typing import NamedTuple

from .types import METERS_PER_MILE, FuelStop, OptimizationResult, Station, VehicleConfig

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
            f"No fuel station found between {gap_start_m / METERS_PER_MILE:.1f} mi "
            f"and {gap_end_m / METERS_PER_MILE:.1f} mi along the route."
        )


# ---------------------------------------------------------------------------
# Internal graph node
# ---------------------------------------------------------------------------

class _Node(NamedTuple):
    """A node in the solver's DAG (start, a fuel station, or the destination)."""

    distance_m: float       # position along route in metres
    price_usd: float        # USD / gallon at this node (0 for start/end)
    station: Station | None  # None for virtual start/end nodes


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _collapse_colocated(stations: list[Station]) -> list[_Node]:
    """
    Merge stations that share the same distance_along_route_m value (within
    1 m tolerance) into one node at the cheapest available price.

    Input must be sorted by distance_along_route_m ascending.
    """
    if not stations:
        return []

    nodes: list[_Node] = []
    current_dist = stations[0].distance_along_route_m
    cheapest: Station = stations[0]

    for s in stations[1:]:
        if math.isclose(s.distance_along_route_m, current_dist, abs_tol=1.0):
            if s.retail_price_usd < cheapest.retail_price_usd:
                cheapest = s
        else:
            nodes.append(_Node(
                distance_m=cheapest.distance_along_route_m,
                price_usd=cheapest.retail_price_usd,
                station=cheapest,
            ))
            current_dist = s.distance_along_route_m
            cheapest = s

    # Flush last group
    nodes.append(_Node(
        distance_m=cheapest.distance_along_route_m,
        price_usd=cheapest.retail_price_usd,
        station=cheapest,
    ))
    return nodes


def _leg_gallons(leg_m: float, mpg: float) -> float:
    """Gallons consumed driving ``leg_m`` metres at ``mpg`` miles per gallon."""
    return (leg_m / METERS_PER_MILE) / mpg


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

    Notes
    -----
    **Short route fast path**: if ``route_distance_m <= vehicle.range_m``
    the vehicle completes the journey on its starting full tank --
    ``fuel_stops`` will be an empty list and ``total_fuel_cost_usd`` will
    be ``0.0``.  This is intentional, not a bug.

    Raises
    ------
    InfeasibleRouteError
        If the route cannot be completed within the vehicle range given
        the available stations (i.e., there is a gap between consecutive
        reachable nodes that exceeds ``vehicle.range_m``).
    """
    candidate_count = len(stations)
    logger.info(
        "Optimising route: %.1f mi, %d candidate stations, range %.0f mi",
        route_distance_m / METERS_PER_MILE,
        candidate_count,
        vehicle.range_miles,
    )

    range_m = vehicle.range_m

    # ------------------------------------------------------------------
    # Fast path: route fits within a single full tank
    # ------------------------------------------------------------------
    if route_distance_m <= range_m:
        logger.debug("Route fits within range -- no fuel stops needed.")
        return OptimizationResult(
            fuel_stops=[],
            total_gallons=0.0,
            total_fuel_cost_usd=0.0,
            route_distance_m=route_distance_m,
            candidate_station_count=candidate_count,
        )

    # ------------------------------------------------------------------
    # Build DAG nodes
    # ------------------------------------------------------------------
    start_node = _Node(distance_m=0.0, price_usd=0.0, station=None)
    end_node = _Node(distance_m=route_distance_m, price_usd=0.0, station=None)

    sorted_stations = sorted(stations, key=lambda s: s.distance_along_route_m)
    middle_nodes = _collapse_colocated(sorted_stations)

    # Full node list in route order: [start, ...stations..., end]
    nodes: list[_Node] = [start_node] + middle_nodes + [end_node]
    n = len(nodes)
    end_idx = n - 1

    # ------------------------------------------------------------------
    # Feasibility pre-check: detect any gap > range_m between adjacent
    # nodes (after collapse).  Because nodes are sorted by distance, a
    # gap can only exist between two consecutive entries.
    # ------------------------------------------------------------------
    for i in range(n - 1):
        if nodes[i + 1].distance_m - nodes[i].distance_m > range_m:
            raise InfeasibleRouteError(
                gap_start_m=nodes[i].distance_m,
                gap_end_m=nodes[i + 1].distance_m,
            )

    # ------------------------------------------------------------------
    # DP over the DAG (topological order = index order)
    # ------------------------------------------------------------------
    # dp[i]   = minimum total fuel cost to arrive at node i
    # prev[i] = index of predecessor node on the optimal path to i
    INF = math.inf
    dp: list[float] = [INF] * n
    prev: list[int] = [-1] * n
    dp[0] = 0.0  # Start: zero cost incurred so far

    for i in range(n - 1):
        if dp[i] == INF:
            continue  # Unreachable node; skip (should not happen post-check)

        for j in range(i + 1, n):
            leg_m = nodes[j].distance_m - nodes[i].distance_m
            if leg_m > range_m:
                break  # Nodes sorted ascending -- nothing further is reachable

            # Fuel cost at node i to cover exactly leg i -> j.
            # The start node has price_usd = 0 so its contribution is $0
            # (the driver arrives full and buys at the first real stop).
            cost = _leg_gallons(leg_m, vehicle.mpg) * nodes[i].price_usd
            candidate = dp[i] + cost
            if candidate < dp[j]:
                dp[j] = candidate
                prev[j] = i

    # ------------------------------------------------------------------
    # Reconstruct optimal path (back-trace from end to start)
    # ------------------------------------------------------------------
    if dp[end_idx] == INF:
        # Defensive: should be caught by feasibility pre-check above.
        raise InfeasibleRouteError(gap_start_m=0.0, gap_end_m=route_distance_m)

    path_indices: list[int] = []
    idx = end_idx
    while idx != 0:
        path_indices.append(idx)
        idx = prev[idx]
    path_indices.append(0)
    path_indices.reverse()  # [0, ..., end_idx]

    # ------------------------------------------------------------------
    # Build FuelStop objects for every real station stop in the path
    # ------------------------------------------------------------------
    fuel_stops: list[FuelStop] = []

    for k in range(len(path_indices) - 1):
        src_idx = path_indices[k]
        dst_idx = path_indices[k + 1]
        src_node = nodes[src_idx]
        dst_node = nodes[dst_idx]

        # Skip virtual start node (no purchase there)
        if src_node.station is None:
            continue

        leg_m = dst_node.distance_m - src_node.distance_m
        gallons = _leg_gallons(leg_m, vehicle.mpg)
        cost = gallons * src_node.price_usd

        fuel_stops.append(FuelStop(
            station=src_node.station,
            gallons=gallons,
            cost_usd=cost,
        ))

    total_gallons = sum(s.gallons for s in fuel_stops)
    total_cost = sum(s.cost_usd for s in fuel_stops)

    logger.info(
        "Optimisation complete: %d stop(s), %.2f gal, $%.2f total",
        len(fuel_stops),
        total_gallons,
        total_cost,
    )

    return OptimizationResult(
        fuel_stops=fuel_stops,
        total_gallons=total_gallons,
        total_fuel_cost_usd=total_cost,
        route_distance_m=route_distance_m,
        candidate_station_count=candidate_count,
    )
