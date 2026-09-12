"""
tests/test_spatial.py
=====================
Unit tests for apps.optimizer.spatial.match_stations_to_route.

Exercises:
- Corridor inclusion/exclusion at boundaries (inside vs outside corridor_miles).
- Polyline vs straight-line distance verification on non-linear routes.
- Sharp hairpin turn handling with k=5 nearest vertex search.
- Zero-length consecutive segment guard (duplicate OSRM waypoint coordinates).
- Co-located stations (same coordinates) receiving identical cumulative distance.
- Ascending sort order of returned Station instances.
- Compatibility with dicts, dataclasses, and ORM-like objects.
- Performance scaling sanity check with 7,000+ stations in < 0.5 seconds.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from apps.optimizer.spatial import (
    CORRIDOR_DEFAULT_MILES,
    match_stations_to_route,
)
from apps.optimizer.types import Station

# Earth coordinates conversion constants
# At equator / mid-latitudes: 1 deg latitude ≈ 69.05 miles
MILES_PER_LAT_DEG = 69.05


def _offset_lat(base_lat: float, miles_north: float) -> float:
    """Offset latitude north by a specified number of miles."""
    return base_lat + (miles_north / MILES_PER_LAT_DEG)


def _offset_lon(base_lat: float, base_lon: float, miles_east: float) -> float:
    """Offset longitude east by a specified number of miles at given latitude."""
    import math

    miles_per_lon_deg = MILES_PER_LAT_DEG * math.cos(math.radians(base_lat))
    return base_lon + (miles_east / miles_per_lon_deg)


class TestMatchStationsToRoute:
    def test_corridor_boundary_inclusion_exclusion(self):
        """
        Straight-line route (West to East along lat 35.0):
        - Station at 9.5 miles perpendicular offset (inside 10-mile corridor) must be included.
        - Station at 10.5 miles perpendicular offset (outside 10-mile corridor) must be excluded.
        """
        base_lat = 35.0
        # Route goes East from lon -100.0 to lon -98.0 (approx 113 miles)
        route = [(-100.0, base_lat), (-99.0, base_lat), (-98.0, base_lat)]

        st_inside = {
            "id": 1,
            "name": "Inside Corridor",
            "city": "Amarillo",
            "state": "TX",
            "latitude": _offset_lat(base_lat, 9.5),  # 9.5 miles North
            "longitude": -99.0,
            "retail_price_usd": 3.49,
        }
        st_outside = {
            "id": 2,
            "name": "Outside Corridor",
            "city": "Pampa",
            "state": "TX",
            "latitude": _offset_lat(base_lat, 10.5),  # 10.5 miles North
            "longitude": -99.0,
            "retail_price_usd": 3.29,
        }

        matched = match_stations_to_route(
            route, [st_inside, st_outside], corridor_miles=10.0
        )

        matched_ids = [s.id for s in matched]
        assert 1 in matched_ids
        assert 2 not in matched_ids
        assert len(matched) == 1

    def test_distance_along_route_follows_polyline_not_straight_line(self):
        """
        L-shaped route:
        Leg 1: (30.0, -100.0) -> East 100 miles
        Leg 2: Turn North 100 miles -> endpoint
        Total route length: ~200 miles.

        A station located near the end of Leg 2:
        - Straight-line distance from route start is ~141.4 miles (hypotenuse).
        - Distance along polyline must be ~200 miles (100 + 100).
        """
        start_lat, start_lon = 30.0, -100.0
        corner_lon = _offset_lon(start_lat, start_lon, 100.0)
        corner_lat = start_lat
        end_lat = _offset_lat(corner_lat, 100.0)
        end_lon = corner_lon

        route = [
            (start_lon, start_lat),
            (corner_lon, corner_lat),
            (end_lon, end_lat),
        ]

        station_near_end = {
            "id": 10,
            "name": "End Stop",
            "city": "Austin",
            "state": "TX",
            "latitude": _offset_lat(end_lat, -1.0),  # 1 mile before end of Leg 2
            "longitude": end_lon,
            "retail_price_usd": 3.19,
        }

        matched = match_stations_to_route(route, [station_near_end], corridor_miles=10.0)
        assert len(matched) == 1
        st = matched[0]

        # Polyline distance should be ~199 miles, NOT the ~141 mile straight-line distance
        assert st.distance_along_route_miles == pytest.approx(199.0, abs=3.0)
        assert st.distance_along_route_miles > 180.0
        # Check canonical meters conversion
        assert st.distance_along_route_m == pytest.approx(st.distance_along_route_miles * 1609.344)

    def test_sharp_turn_hairpin_projection(self):
        """
        Route with a sharp hairpin turn and closely-spaced vertices around the bend.
        Tests that k=5 nearest vertex search correctly inspects incident segments
        and avoids misprojecting to the wrong segment across the hairpin.
        """
        # Route goes East 50 miles, makes a tight 180-degree hairpin turn, and goes West 50 miles
        p0 = (-100.0, 35.0)
        p1 = (-99.5, 35.0)
        p2 = (-99.0, 35.0)
        # Hairpin bend cluster
        p3 = (-99.0, 35.02)
        p4 = (-99.05, 35.04)
        p5 = (-99.1, 35.04)
        # Returning westward
        p6 = (-99.5, 35.04)
        p7 = (-100.0, 35.04)

        route = [p0, p1, p2, p3, p4, p5, p6, p7]

        # Station placed near returning leg p6
        st_return = {
            "id": 99,
            "name": "Return Stop",
            "city": "Clinton",
            "state": "OK",
            "latitude": 35.04,
            "longitude": -99.5,
            "retail_price_usd": 3.05,
        }

        matched = match_stations_to_route(route, [st_return], corridor_miles=10.0)
        assert len(matched) == 1
        st = matched[0]
        # Should project to the return leg (> 50 miles cumulative), not the outgoing leg
        assert st.distance_along_route_miles > 50.0

    def test_zero_length_duplicate_consecutive_points(self):
        """
        Route containing duplicate consecutive coordinates (zero-length segments).
        Must not raise ZeroDivisionError and must project correctly.
        """
        p0 = (-100.0, 35.0)
        p1 = (-99.0, 35.0)
        p1_dup = (-99.0, 35.0)  # Duplicate consecutive coordinate
        p2 = (-98.0, 35.0)

        route = [p0, p1, p1_dup, p2]

        st = {
            "id": 42,
            "name": "Midpoint Stop",
            "city": "Weatherford",
            "state": "OK",
            "latitude": 35.0,
            "longitude": -99.0,
            "retail_price_usd": 3.10,
        }

        matched = match_stations_to_route(route, [st], corridor_miles=10.0)
        assert len(matched) == 1
        assert matched[0].id == 42
        assert matched[0].distance_along_route_miles > 0.0

    def test_colocated_stations_share_same_distance(self):
        """
        Multiple stations sharing identical lat/lon (e.g. same exit/city)
        must receive the exact same distance_along_route_miles by construction.
        """
        route = [(-100.0, 35.0), (-98.0, 35.0)]

        st1 = {"id": 101, "name": "Pilot #1", "city": "Elk City", "state": "OK", "latitude": 35.01, "longitude": -99.2, "retail_price_usd": 3.25}
        st2 = {"id": 102, "name": "Love's #2", "city": "Elk City", "state": "OK", "latitude": 35.01, "longitude": -99.2, "retail_price_usd": 3.15}
        st3 = {"id": 103, "name": "Flying J #3", "city": "Elk City", "state": "OK", "latitude": 35.01, "longitude": -99.2, "retail_price_usd": 3.35}

        matched = match_stations_to_route(route, [st1, st2, st3], corridor_miles=10.0)
        assert len(matched) == 3

        dists = [s.distance_along_route_miles for s in matched]
        assert dists[0] == dists[1] == dists[2]
        dists_m = [s.distance_along_route_m for s in matched]
        assert dists_m[0] == dists_m[1] == dists_m[2]

    def test_returned_stations_sorted_ascending(self):
        """
        Stations scattered along a route must be returned in strictly sorted order
        by distance_along_route_miles ascending.
        """
        route = [(-100.0, 35.0), (-95.0, 35.0)]

        # Stations at longitudes -96.0, -99.0, -97.0 (passed in out-of-order)
        s_late = {"id": 1, "name": "Late", "city": "East", "state": "OK", "latitude": 35.0, "longitude": -96.0, "retail_price_usd": 3.50}
        s_early = {"id": 2, "name": "Early", "city": "West", "state": "OK", "latitude": 35.0, "longitude": -99.0, "retail_price_usd": 3.50}
        s_mid = {"id": 3, "name": "Mid", "city": "Central", "state": "OK", "latitude": 35.0, "longitude": -97.0, "retail_price_usd": 3.50}

        matched = match_stations_to_route(route, [s_late, s_early, s_mid], corridor_miles=10.0)
        assert [s.id for s in matched] == [2, 3, 1]

        dists = [s.distance_along_route_miles for s in matched]
        assert dists == sorted(dists)

    def test_handles_orm_models_and_dataclasses(self):
        """Verifies function works with ORM mock objects and Station dataclasses."""
        route = [(-100.0, 35.0), (-98.0, 35.0)]

        # Mock FuelStation ORM object
        orm_mock = MagicMock()
        orm_mock.id = 501
        orm_mock.name = "ORM Station"
        orm_mock.city = "Sayre"
        orm_mock.state = "OK"
        orm_mock.latitude = 35.0
        orm_mock.longitude = -99.5
        orm_mock.price = 3.39

        # Station dataclass
        dc_station = Station(
            id=502,
            name="DC Station",
            city="Canute",
            state="OK",
            latitude=35.0,
            longitude=-99.2,
            retail_price_usd=3.29,
        )

        matched = match_stations_to_route(route, [orm_mock, dc_station], corridor_miles=10.0)
        assert len(matched) == 2
        assert isinstance(matched[0], Station)
        assert isinstance(matched[1], Station)
        assert matched[0].id == 501
        assert matched[1].id == 502

    def test_performance_against_full_station_volume(self):
        """
        Scalability sanity check:
        7,000 synthetic stations (~full dataset volume) matched against a
        1,000-point route polyline must finish in well under 1 second (< 0.5s).
        """
        import numpy as np

        # Generate 1,000-point synthetic route (e.g. cross-country US)
        t = np.linspace(0, 1, 1000)
        route_lons = -105.0 + 20.0 * t + 0.1 * np.sin(10 * t)
        route_lats = 35.0 + 5.0 * t + 0.1 * np.cos(10 * t)
        route = list(zip(route_lons, route_lats))

        # Generate 7,000 synthetic stations randomly scattered in bounding region
        np.random.seed(42)
        station_lons = np.random.uniform(-106.0, -84.0, 7000)
        station_lats = np.random.uniform(34.0, 41.0, 7000)

        stations = [
            {
                "id": i,
                "name": f"Station {i}",
                "city": f"City {i % 100}",
                "state": "US",
                "latitude": float(station_lats[i]),
                "longitude": float(station_lons[i]),
                "retail_price_usd": 3.50,
            }
            for i in range(7000)
        ]

        start_time = time.perf_counter()
        matched = match_stations_to_route(route, stations, corridor_miles=CORRIDOR_DEFAULT_MILES)
        elapsed = time.perf_counter() - start_time

        assert isinstance(matched, list)
        assert len(matched) > 0
        # Assert sub-second execution (typically ~0.05-0.10s)
        assert elapsed < 0.5, f"Expected matching 7,000 stations to take < 0.5s, took {elapsed:.3f}s"
