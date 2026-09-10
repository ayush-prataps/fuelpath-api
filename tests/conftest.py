"""
tests/conftest.py
=================
Shared pytest fixtures for the fuelpath-api test suite.
"""

import pytest

from apps.optimizer.types import Station, VehicleConfig


# ---------------------------------------------------------------------------
# Reusable VehicleConfig
# ---------------------------------------------------------------------------

@pytest.fixture
def default_vehicle() -> VehicleConfig:
    """500-mile range, 10 mpg — matches the project spec."""
    return VehicleConfig(range_miles=500, mpg=10)


# ---------------------------------------------------------------------------
# Sample stations
# ---------------------------------------------------------------------------

@pytest.fixture
def station_factory():
    """
    Returns a factory function that builds Station dataclasses.

    Usage:
        station = station_factory(distance_along_route_m=100_000, retail_price_usd=3.50)
    """
    _counter = {"n": 0}

    def _make(
        distance_along_route_m: float = 0.0,
        retail_price_usd: float = 3.50,
        **kwargs,
    ) -> Station:
        _counter["n"] += 1
        n = _counter["n"]
        defaults = dict(
            id=n,
            name=f"Truck Stop #{n}",
            city=f"City {n}",
            state="TX",
            latitude=30.0 + n * 0.1,
            longitude=-97.0 + n * 0.1,
            retail_price_usd=retail_price_usd,
            distance_along_route_m=distance_along_route_m,
        )
        defaults.update(kwargs)
        return Station(**defaults)

    return _make


@pytest.fixture
def linear_stations(station_factory) -> list[Station]:
    """
    Five stations evenly spaced ~100 miles apart along a 500-mile route.
    Prices alternate cheap/expensive to give the optimizer something to
    prefer.
    """
    return [
        station_factory(distance_along_route_m=i * 160_934, retail_price_usd=p)
        for i, p in enumerate(
            [3.20, 3.80, 3.10, 3.90, 3.15], start=1
        )
    ]
