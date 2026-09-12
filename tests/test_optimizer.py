"""
tests/test_optimizer.py
========================
Unit tests for apps.optimizer.solver (and supporting types).

These tests exercise the solver in pure Python — no DB, no HTTP, no Django
settings required.  They will be skipped / xfail until the solver is
implemented; the stubs demonstrate the expected behaviour and serve as a
living specification.
"""

import pytest

from apps.optimizer.solver import InfeasibleRouteError, optimise
from apps.optimizer.types import OptimizationResult, VehicleConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MILES_TO_M = 1_609.344


def km(miles: float) -> float:
    """Convert miles to metres for readability in test params."""
    return miles * MILES_TO_M


# ---------------------------------------------------------------------------
# Type / contract tests (no NotImplementedError — always run)
# ---------------------------------------------------------------------------

class TestVehicleConfig:
    def test_range_m_conversion(self):
        v = VehicleConfig(range_miles=500, mpg=10)
        assert abs(v.range_m - km(500)) < 1

    def test_tank_gallons(self):
        v = VehicleConfig(range_miles=500, mpg=10)
        assert v.tank_gallons == pytest.approx(50.0)

    def test_custom_mpg(self):
        v = VehicleConfig(range_miles=300, mpg=15)
        assert v.tank_gallons == pytest.approx(20.0)


class TestOptimizationResult:
    def test_defaults(self):
        result = OptimizationResult()
        assert result.fuel_stops == []
        assert result.total_gallons == 0.0
        assert result.total_fuel_cost_usd == 0.0


# ---------------------------------------------------------------------------
# Solver behaviour tests (marked xfail until implemented)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Solver behaviour tests
# ---------------------------------------------------------------------------

class TestOptimiseSolver:
    def test_returns_optimization_result(self, default_vehicle, linear_stations):
        """optimise() must return an OptimizationResult instance."""
        result = optimise(
            route_distance_m=km(800),
            stations=linear_stations,
            vehicle=default_vehicle,
        )
        assert isinstance(result, OptimizationResult)

    def test_no_stops_needed_short_route(self, default_vehicle, station_factory):
        """
        A route shorter than the vehicle range needs zero fuel stops.
        The driver starts with a full tank and arrives without refuelling.
        This can look like a bug — it is intentional, not a defect.
        """
        result = optimise(
            route_distance_m=km(200),   # well within 500-mile range
            stations=[station_factory(distance_along_route_m=km(100))],
            vehicle=default_vehicle,
        )
        assert result.fuel_stops == []
        assert result.total_fuel_cost_usd == 0.0

    def test_stops_are_sorted_by_distance(self, default_vehicle, linear_stations):
        """Selected stops must be returned in route order."""
        result = optimise(
            route_distance_m=km(800),
            stations=linear_stations,
            vehicle=default_vehicle,
        )
        distances = [s.station.distance_along_route_m for s in result.fuel_stops]
        assert distances == sorted(distances)

    def test_total_cost_equals_sum_of_stop_costs(self, default_vehicle, linear_stations):
        """total_fuel_cost_usd must equal the sum of individual stop costs."""
        result = optimise(
            route_distance_m=km(800),
            stations=linear_stations,
            vehicle=default_vehicle,
        )
        expected = sum(s.cost_usd for s in result.fuel_stops)
        assert result.total_fuel_cost_usd == pytest.approx(expected, rel=1e-6)

    def test_infeasible_route_raises(self, default_vehicle):
        """
        A route with a gap > vehicle range and no stations in between
        must raise InfeasibleRouteError.
        """
        with pytest.raises(InfeasibleRouteError):
            optimise(
                route_distance_m=km(1_500),
                stations=[],            # no stations at all
                vehicle=default_vehicle,
            )

    def test_prefers_cheaper_stations(self, default_vehicle, station_factory):
        """
        Given two stations in range, the optimizer must prefer the one
        with the lower price.
        """
        cheap = station_factory(distance_along_route_m=km(200), retail_price_usd=2.99)
        expensive = station_factory(distance_along_route_m=km(201), retail_price_usd=4.99)
        result = optimise(
            # 600mi > 500mi range, so the vehicle must refuel at least once
            route_distance_m=km(600),
            stations=[cheap, expensive],
            vehicle=default_vehicle,
        )
        stop_ids = [s.station.id for s in result.fuel_stops]
        assert cheap.id in stop_ids
        assert expensive.id not in stop_ids

    def test_avoids_greedy_trap(self, default_vehicle, station_factory):
        """
        Non-greedy test: a greedy algorithm would stop at station A (close,
        cheap enough) and then be forced to stop at station B (expensive).
        The DP solver must find it is cheaper overall to skip A and stop
        only at C (further but cheaper).

        Route: 0 ----[A@200mi $3.00]----[B@350mi $4.50]----[C@400mi $2.50]---- 600mi
        Range: 500 miles.

        Greedy picks A (cheap, within range), then must stop at B or C.
        From A (200mi) to destination (600mi) = 400mi remaining — both B
        and C are reachable.  From A, the greedy picks C ($2.50) next.
        That path: cost = (200mi / 10mpg) * $3.00 + (400mi / 10mpg) * $2.50
                         = 20 * 3.00 + 40 * 2.50 = $60 + $100 = $160.

        Optimal: skip A entirely, stop only at C (400mi from start, within
        range), then drive the last 200mi on what was bought at C.
        Cost = (400mi / 10mpg) * $2.50 + (200mi / 10mpg) * $2.50
             = 40 * 2.50 + 20 * 2.50 = $100 + $50 = $150.

        Wait -- let's recalculate the greedy more carefully:
          Start full (500mi of fuel). Drive to A (200mi). Tank has 300mi left.
          At A buy enough to reach C: need 200mi more → buy 20gal @ $3.00 = $60.
          Drive to C (200mi). Tank has 300mi left (enough for remaining 200mi).
          No more stops needed. Total = $60 + $0 at C (full enough) = $60.

        To make the trap concrete, use a tighter range and forced stops.
        Route: 900mi, range 500mi.
          A @ 200mi, $3.50   ← greedy picks this (only stop reachable from 0..500)
          B @ 450mi, $2.00   ← further away but cheaper per gallon
          C @ 700mi, $3.80   ← must reach end (900mi) from here or from B

        From start (range 500mi): can reach A (200), B (450) — but NOT C (700>500).
        Greedy picks A (first/closest in range); then from A (200mi), range 500mi
        → can reach B (250mi away) and C (500mi away exactly).
        Greedy might stop at B next (cheapest visible). From B (450mi), range 500mi
        → can reach C (250mi) and end (450mi ≤ 500).  Stops at B then drives home.
        Greedy path: buy at A + B.  A: enough to reach B (250mi) = 25gal @ $3.50 = $87.50
                                    B: enough to reach end (450mi) = 45gal @ $2.00 = $90.00
                                    Total greedy = $177.50.

        Optimal DP: skip A, stop only at B.
          From start, go directly to B (450mi, within 500mi range).
          Buy at B: enough for remaining 450mi = 45gal @ $2.00 = $90.00.
          Total optimal = $90.00.
        """
        vehicle = VehicleConfig(range_miles=500, mpg=10)
        a = station_factory(distance_along_route_m=km(200), retail_price_usd=3.50)
        b = station_factory(distance_along_route_m=km(450), retail_price_usd=2.00)
        c = station_factory(distance_along_route_m=km(700), retail_price_usd=3.80)

        result = optimise(
            route_distance_m=km(900),
            stations=[a, b, c],
            vehicle=vehicle,
        )

        stop_ids = [s.station.id for s in result.fuel_stops]
        # Optimal path: skip A, stop at B, stop at C (needed for last 200mi)
        # From B (450mi), can reach 450+500=950mi > 900mi end, so only B needed.
        assert a.id not in stop_ids, "Solver fell into greedy trap: stopped at expensive A"
        assert b.id in stop_ids, "Solver must stop at cheap B"
        # Total cost must be strictly less than the greedy solution ($177.50)
        assert result.total_fuel_cost_usd < 177.50

    def test_colocated_stations_collapsed_to_cheapest(self, default_vehicle, station_factory):
        """
        Two stations at the same mileage marker must be collapsed to the
        cheaper one — the expensive co-located station must not appear in
        the result.
        """
        cheap = station_factory(
            distance_along_route_m=km(300),
            retail_price_usd=2.80,
        )
        expensive = station_factory(
            # 0.5 m apart — same position within collapse tolerance
            distance_along_route_m=km(300) + 0.5,
            retail_price_usd=4.20,
        )
        result = optimise(
            route_distance_m=km(800),
            stations=[cheap, expensive],
            vehicle=default_vehicle,
        )
        stop_ids = [s.station.id for s in result.fuel_stops]
        assert cheap.id in stop_ids
        assert expensive.id not in stop_ids

    def test_performance_large_station_set(self, station_factory):
        """
        Solver must complete in under 1 second for a realistic dataset:
        1 000 stations spread across a 3 000-mile route.
        """
        import time

        vehicle = VehicleConfig(range_miles=500, mpg=10)
        n = 1_000
        step_miles = 3_000 / (n + 1)
        stations = [
            station_factory(
                distance_along_route_m=km(step_miles * (i + 1)),
                retail_price_usd=2.50 + (i % 10) * 0.10,  # vary prices
            )
            for i in range(n)
        ]

        start = time.perf_counter()
        result = optimise(
            route_distance_m=km(3_000),
            stations=stations,
            vehicle=vehicle,
        )
        elapsed = time.perf_counter() - start

        assert elapsed < 1.0, f"Solver too slow: {elapsed:.3f}s for {n} stations"
        assert isinstance(result, OptimizationResult)
        assert len(result.fuel_stops) > 0

