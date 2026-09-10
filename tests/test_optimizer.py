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

@pytest.mark.xfail(raises=NotImplementedError, strict=True, reason="solver not yet implemented")
class TestOptimiseSolver:
    def test_returns_optimization_result(self, default_vehicle, linear_stations):
        """optimise() must return an OptimizationResult instance."""
        result = optimise(
            route_distance_m=km(500),
            stations=linear_stations,
            vehicle=default_vehicle,
        )
        assert isinstance(result, OptimizationResult)

    def test_no_stops_needed_short_route(self, default_vehicle, station_factory):
        """
        A route shorter than the vehicle range needs zero fuel stops
        (driver starts with a full tank).
        """
        result = optimise(
            route_distance_m=km(200),   # well within 500-mile range
            stations=[station_factory(distance_along_route_m=km(100))],
            vehicle=default_vehicle,
        )
        assert result.fuel_stops == []

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
            route_distance_m=km(500),
            stations=[cheap, expensive],
            vehicle=default_vehicle,
        )
        stop_ids = [s.station.id for s in result.fuel_stops]
        assert cheap.id in stop_ids
        assert expensive.id not in stop_ids
