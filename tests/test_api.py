"""
tests/test_api.py
==================
Integration tests for POST /api/v1/route/ via DRF's APIClient.

All external calls (OSRM, Nominatim, DB) are mocked — zero real network
requests or database access in this file.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from apps.optimizer.solver import InfeasibleRouteError
from apps.optimizer.types import FuelStop, OptimizationResult, Station, VehicleConfig
from apps.routing.exceptions import (
    LocationNotFoundError,
    LocationOutsideUSError,
    NoRouteFoundError,
    RoutingTimeoutError,
    RoutingUnreachableError,
)
from apps.routing.service import Coordinate, RouteResult


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def api_client() -> APIClient:
    return APIClient()


@pytest.fixture
def valid_payload() -> dict:
    return {"start": "Chicago, IL", "finish": "Houston, TX"}


# Realistic OSRM route result (Chicago → Houston, ~1,085 miles)
CHICAGO = Coordinate(latitude=41.8781, longitude=-87.6298)
HOUSTON = Coordinate(latitude=29.7604, longitude=-95.3698)

SAMPLE_GEOJSON = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": [
                    [-87.6298, 41.8781],
                    [-89.1234, 38.5678],
                    [-95.3698, 29.7604],
                ],
            },
            "properties": {"distance_m": 1_746_200.0, "duration_s": 58320.0},
        }
    ],
}

SAMPLE_ROUTE_RESULT = RouteResult(
    geojson=SAMPLE_GEOJSON,
    distance_miles=1085.04,
    duration_s=58320.0,
    waypoints=[CHICAGO, HOUSTON],
)

# Two mock stations within the corridor (both CONUS)
STATION_A = Station(
    id=1,
    name="Pilot Travel Center",
    city="Memphis",
    state="TN",
    latitude=35.149,
    longitude=-90.048,
    retail_price_usd=3.45,
    distance_along_route_m=500_000.0,
)
STATION_B = Station(
    id=2,
    name="Love's Travel Stop",
    city="Little Rock",
    state="AR",
    latitude=34.746,
    longitude=-92.289,
    retail_price_usd=3.20,
    distance_along_route_m=900_000.0,
)

SAMPLE_VEHICLE = VehicleConfig(range_miles=500, mpg=10)

SAMPLE_OPT_RESULT = OptimizationResult(
    fuel_stops=[
        FuelStop(station=STATION_A, gallons=32.5, cost_usd=112.13),
        FuelStop(station=STATION_B, gallons=25.0, cost_usd=80.00),
    ],
    total_gallons=57.5,
    total_fuel_cost_usd=192.13,
    route_distance_m=SAMPLE_ROUTE_RESULT.distance_m,
    candidate_station_count=2,
)


def _mock_db_stations():
    """Return a queryset-like mock yielding two FuelStation ORM objects."""
    def _make_orm(s: Station):
        m = MagicMock()
        m.id = s.id
        m.name = s.name
        m.city = s.city
        m.state = s.state
        m.latitude = s.latitude
        m.longitude = s.longitude
        m.price = s.retail_price_usd
        return m

    qs = MagicMock()
    qs.filter.return_value = qs
    qs.only.return_value = [_make_orm(STATION_A), _make_orm(STATION_B)]
    return qs


def _patch_pipeline(
    *,
    geocode_side_effect=None,
    geocode_return=None,
    route_result=SAMPLE_ROUTE_RESULT,
    matched_stations=None,
    opt_result=SAMPLE_OPT_RESULT,
):
    """
    Context-manager stack that patches the full hot-path pipeline.
    Returns a dict of mock objects keyed by name for assertions.
    """
    if matched_stations is None:
        matched_stations = [STATION_A, STATION_B]

    mocks = {}

    def enter(mock_name, *patch_args, **patch_kwargs):
        p = patch(*patch_args, **patch_kwargs)
        m = p.start()
        mocks[mock_name] = m
        return m

    enter("geocode", "apps.fuel.views.geocode_location",
          side_effect=geocode_side_effect,
          return_value=geocode_return or (41.8781, -87.6298))
    enter("get_route", "apps.fuel.views.get_route", return_value=route_result)
    enter("FuelStation", "apps.fuel.views.FuelStation")
    mocks["FuelStation"].objects = _mock_db_stations()
    enter("match", "apps.fuel.views.match_stations_to_route",
          return_value=matched_stations)
    enter("optimise", "apps.fuel.views.optimise", return_value=opt_result)

    return mocks


class _PipelinePatcher:
    """Simple context manager wrapping _patch_pipeline."""

    def __init__(self, **kwargs):
        self._kwargs = kwargs
        self._patches = []
        self.mocks = {}

    def __enter__(self):
        from unittest.mock import patch as _patch, MagicMock

        matched_stations = self._kwargs.get("matched_stations", [STATION_A, STATION_B])
        opt_result       = self._kwargs.get("opt_result", SAMPLE_OPT_RESULT)
        route_result     = self._kwargs.get("route_result", SAMPLE_ROUTE_RESULT)
        geocode_se       = self._kwargs.get("geocode_side_effect", None)
        geocode_rv       = self._kwargs.get("geocode_return", None)

        def _start(target, **kw):
            p = _patch(target, **kw)
            m = p.start()
            self._patches.append(p)
            return m

        if geocode_se is not None:
            self.mocks["geocode"] = _start("apps.fuel.views.geocode_location",
                                           side_effect=geocode_se)
        else:
            call_count = [0]
            returns = [
                geocode_rv or (41.8781, -87.6298),
                (29.7604, -95.3698),
            ]
            def _geocode_calls(query):
                idx = min(call_count[0], len(returns) - 1)
                call_count[0] += 1
                return returns[idx]
            self.mocks["geocode"] = _start("apps.fuel.views.geocode_location",
                                           side_effect=_geocode_calls)

        self.mocks["get_route"] = _start("apps.fuel.views.get_route",
                                         return_value=route_result)

        qs_mock = MagicMock()
        qs_mock.filter.return_value = qs_mock

        def _make_orm(s):
            m = MagicMock()
            m.id, m.name, m.city, m.state = s.id, s.name, s.city, s.state
            m.latitude, m.longitude = s.latitude, s.longitude
            m.price = s.retail_price_usd
            return m

        qs_mock.only.return_value = [_make_orm(s) for s in matched_stations]
        fs_mock = _start("apps.fuel.views.FuelStation")
        fs_mock.objects = qs_mock
        self.mocks["FuelStation"] = fs_mock

        self.mocks["match"] = _start("apps.fuel.views.match_stations_to_route",
                                     return_value=matched_stations)
        self.mocks["optimise"] = _start("apps.fuel.views.optimise",
                                        return_value=opt_result)
        return self.mocks

    def __exit__(self, *args):
        for p in reversed(self._patches):
            p.stop()


# ---------------------------------------------------------------------------
# Input validation tests (always run — no external deps needed)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestRouteEndpointValidation:
    URL = "/api/v1/route/"

    def test_missing_start_returns_400(self, api_client):
        response = api_client.post(self.URL, {"finish": "Houston, TX"}, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "error" in response.data

    def test_missing_finish_returns_400(self, api_client):
        response = api_client.post(self.URL, {"start": "Chicago, IL"}, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "error" in response.data

    def test_empty_body_returns_400(self, api_client):
        response = api_client.post(self.URL, {}, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_get_method_not_allowed(self, api_client):
        response = api_client.get(self.URL)
        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    def test_valid_payload_returns_json(self, api_client, valid_payload):
        """With valid input the endpoint must always respond with JSON."""
        with _PipelinePatcher():
            response = api_client.post(self.URL, valid_payload, format="json")
        assert response["Content-Type"] == "application/json"
        assert response.status_code == status.HTTP_200_OK

    def test_whitespace_only_start_returns_400(self, api_client):
        """All-whitespace start must be rejected with 400 after strip."""
        response = api_client.post(
            self.URL, {"start": "   ", "finish": "Houston, TX"}, format="json"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST


# ---------------------------------------------------------------------------
# Success-path tests (no xfail — real implementation wired)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestRouteEndpointSuccess:
    URL = "/api/v1/route/"

    def test_response_shape(self, api_client, valid_payload):
        """Response must contain route, fuel, and fuel_stops top-level keys."""
        with _PipelinePatcher():
            response = api_client.post(self.URL, valid_payload, format="json")
        assert response.status_code == status.HTTP_200_OK
        data = response.data
        assert "route" in data
        assert "fuel" in data
        assert "fuel_stops" in data
        # route sub-keys
        assert "distance_miles" in data["route"]
        assert "duration_minutes" in data["route"]
        assert "geometry" in data["route"]
        # fuel sub-keys
        assert "total_gallons" in data["fuel"]
        assert "total_cost_usd" in data["fuel"]

    def test_route_is_geojson_feature_collection(self, api_client, valid_payload):
        """geometry in route must be a GeoJSON FeatureCollection."""
        with _PipelinePatcher():
            response = api_client.post(self.URL, valid_payload, format="json")
        geom = response.data["route"]["geometry"]
        assert geom["type"] == "FeatureCollection"
        assert "features" in geom

    def test_fuel_stops_are_in_usa(self, api_client, valid_payload):
        """All returned fuel stops must have CONUS-bounded lat/lon."""
        with _PipelinePatcher():
            response = api_client.post(self.URL, valid_payload, format="json")
        for stop in response.data["fuel_stops"]:
            assert 24.0 <= stop["latitude"] <= 50.0
            assert -125.0 <= stop["longitude"] <= -66.0

    def test_totals_are_consistent(self, api_client, valid_payload):
        """fuel.total_gallons and total_cost_usd must match the sum of stops."""
        with _PipelinePatcher():
            response = api_client.post(self.URL, valid_payload, format="json")
        stops = response.data["fuel_stops"]
        expected_gallons = sum(s["gallons_purchased"] for s in stops)
        expected_cost = sum(s["cost_usd"] for s in stops)
        assert response.data["fuel"]["total_gallons"] == pytest.approx(
            expected_gallons, rel=1e-3
        )
        assert response.data["fuel"]["total_cost_usd"] == pytest.approx(
            expected_cost, rel=1e-3
        )

    def test_stops_have_sequence_numbers(self, api_client, valid_payload):
        """Each fuel stop must have a 1-based sequence number."""
        with _PipelinePatcher():
            response = api_client.post(self.URL, valid_payload, format="json")
        stops = response.data["fuel_stops"]
        for i, stop in enumerate(stops, start=1):
            assert stop["sequence"] == i

    def test_money_rounded_to_two_decimals(self, api_client, valid_payload):
        """cost_usd and total_cost_usd must not have more than 2 decimal places."""
        with _PipelinePatcher():
            response = api_client.post(self.URL, valid_payload, format="json")
        for stop in response.data["fuel_stops"]:
            val = stop["cost_usd"]
            assert val == round(val, 2), f"cost_usd has excess precision: {val}"
        total = response.data["fuel"]["total_cost_usd"]
        assert total == round(total, 2)

    def test_distances_rounded_to_one_decimal(self, api_client, valid_payload):
        """distance_from_start_miles and route distance_miles must be 1-decimal."""
        with _PipelinePatcher():
            response = api_client.post(self.URL, valid_payload, format="json")
        d = response.data["route"]["distance_miles"]
        assert d == round(d, 1)
        for stop in response.data["fuel_stops"]:
            v = stop["distance_from_start_miles"]
            assert v == round(v, 1)


# ---------------------------------------------------------------------------
# Error-mapping tests
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestRouteEndpointErrors:
    URL = "/api/v1/route/"

    def test_location_not_found_returns_400(self, api_client, valid_payload):
        with _PipelinePatcher(
            geocode_side_effect=LocationNotFoundError("Could not resolve location.")
        ):
            response = api_client.post(self.URL, valid_payload, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error"]["code"] == "location_not_found"

    def test_location_outside_us_returns_400(self, api_client, valid_payload):
        with _PipelinePatcher(
            geocode_side_effect=LocationOutsideUSError("Location is outside CONUS.")
        ):
            response = api_client.post(self.URL, valid_payload, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error"]["code"] == "location_outside_us"

    def test_no_route_found_returns_404(self, api_client, valid_payload):
        with _PipelinePatcher():
            with patch("apps.fuel.views.get_route",
                       side_effect=NoRouteFoundError("No route.")):
                response = api_client.post(self.URL, valid_payload, format="json")
        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert response.data["error"]["code"] == "no_route_found"

    def test_routing_timeout_returns_504(self, api_client, valid_payload):
        with _PipelinePatcher():
            with patch("apps.fuel.views.get_route",
                       side_effect=RoutingTimeoutError("Timeout.")):
                response = api_client.post(self.URL, valid_payload, format="json")
        assert response.status_code == status.HTTP_504_GATEWAY_TIMEOUT
        assert response.data["error"]["code"] == "routing_timeout"

    def test_routing_unreachable_returns_502(self, api_client, valid_payload):
        with _PipelinePatcher():
            with patch("apps.fuel.views.get_route",
                       side_effect=RoutingUnreachableError("OSRM down.")):
                response = api_client.post(self.URL, valid_payload, format="json")
        assert response.status_code == status.HTTP_502_BAD_GATEWAY
        assert response.data["error"]["code"] == "routing_unreachable"

    def test_infeasible_route_returns_422(self, api_client, valid_payload):
        """
        InfeasibleRouteError (gap between stations > vehicle range) must map
        to HTTP 422 Unprocessable Entity with code 'infeasible_route'.
        This covers e.g. a remote destination with no truck stops in range.
        """
        with _PipelinePatcher():
            with patch(
                "apps.fuel.views.optimise",
                side_effect=InfeasibleRouteError(
                    gap_start_m=0.0, gap_end_m=900_000.0
                ),
            ):
                response = api_client.post(self.URL, valid_payload, format="json")
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        assert response.data["error"]["code"] == "infeasible_route"


# ---------------------------------------------------------------------------
# Short-route fast path (via HTTP layer)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestShortRouteFastPath:
    URL = "/api/v1/route/"

    def test_short_route_returns_empty_fuel_stops_and_zero_cost(
        self, api_client, valid_payload
    ):
        """
        When the route distance is within the vehicle's range (≤ 500 mi),
        the solver returns zero stops and $0.00 cost.  This must flow through
        the HTTP layer correctly — not a 500, not a missing key.
        """
        short_route = RouteResult(
            geojson=SAMPLE_GEOJSON,
            distance_miles=200.0,   # well within 500-mile range
            duration_s=10_800.0,
            waypoints=[CHICAGO, HOUSTON],
        )
        zero_result = OptimizationResult(
            fuel_stops=[],
            total_gallons=0.0,
            total_fuel_cost_usd=0.0,
            route_distance_m=short_route.distance_m,
            candidate_station_count=0,
        )
        with _PipelinePatcher(route_result=short_route, opt_result=zero_result,
                              matched_stations=[]):
            response = api_client.post(self.URL, valid_payload, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert response.data["fuel_stops"] == []
        assert response.data["fuel"]["total_gallons"] == 0.0
        assert response.data["fuel"]["total_cost_usd"] == 0.0


# ---------------------------------------------------------------------------
# Response-time sanity check (mocked — not a real timing benchmark)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestResponseTimeSanity:
    URL = "/api/v1/route/"

    def test_mocked_pipeline_responds_under_2s(self, api_client, valid_payload):
        """
        With all external calls mocked the view must respond in < 2 s.
        This is a sanity check on view-layer overhead, not a benchmark of
        OSRM or Nominatim (which are network-bound and cannot be asserted here).
        """
        with _PipelinePatcher():
            t0 = time.perf_counter()
            response = api_client.post(self.URL, valid_payload, format="json")
            elapsed = time.perf_counter() - t0

        assert response.status_code == status.HTTP_200_OK
        assert elapsed < 2.0, f"View took {elapsed:.3f}s with everything mocked"
