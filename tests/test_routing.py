"""
tests/test_routing.py
=====================
Unit tests for the OSRM routing client and Nominatim geocoding service.

All external network calls (OSRM HTTP requests and Nominatim geocoding calls)
are strictly mocked — zero real network requests are made.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest
from django.core.cache import cache

from apps.routing.exceptions import (
    GeocodingError,
    LocationNotFoundError,
    LocationOutsideUSError,
    NoRouteFoundError,
    RoutingTimeoutError,
    RoutingUnreachableError,
)
from apps.routing.geocoding import (
    NominatimGeocoder,
    geocode_location,
    is_in_conus,
)
from apps.routing.service import (
    Coordinate,
    RouteResult,
    get_route,
)

# ---------------------------------------------------------------------------
# Sample Test Coordinates & Fixtures
# ---------------------------------------------------------------------------

# Valid CONUS points: Chicago, IL and Houston, TX
CHICAGO = Coordinate(latitude=41.8781, longitude=-87.6298)
HOUSTON = Coordinate(latitude=29.7604, longitude=-95.3698)

# Non-CONUS points: London (UK) and Honolulu (HI)
LONDON = Coordinate(latitude=51.5074, longitude=-0.1278)
HONOLULU = Coordinate(latitude=21.3069, longitude=-157.8583)

SAMPLE_OSRM_SUCCESS = {
    "code": "Ok",
    "routes": [
        {
            "geometry": {
                "coordinates": [
                    [-87.6298, 41.8781],
                    [-89.1234, 38.5678],
                    [-95.3698, 29.7604],
                ],
                "type": "LineString",
            },
            "legs": [],
            "distance": 1746200.0,  # ~1,085.04 miles
            "duration": 58320.0,    # ~16.2 hours
            "weight_name": "routability",
            "weight": 58320.0,
        }
    ],
    "waypoints": [
        {"location": [-87.6298, 41.8781], "name": "Chicago"},
        {"location": [-95.3698, 29.7604], "name": "Houston"},
    ],
}


@pytest.fixture(autouse=True)
def clear_cache():
    """Ensure clean cache before and after every test."""
    cache.clear()
    yield
    cache.clear()


# ---------------------------------------------------------------------------
# OSRM Routing Client Tests
# ---------------------------------------------------------------------------

class TestOSRMClient:
    @patch("apps.routing.service._http_get_with_retry")
    def test_successful_route(self, mock_http):
        """Successful OSRM call returns mile-denominated RouteResult with GeoJSON."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = SAMPLE_OSRM_SUCCESS
        mock_http.return_value = mock_resp

        result = get_route(CHICAGO, HOUSTON)

        assert isinstance(result, RouteResult)
        assert result.geojson["type"] == "FeatureCollection"
        assert len(result.geojson["features"]) == 1
        assert result.geojson["features"][0]["geometry"]["type"] == "LineString"

        # Check mile conversion: 1,746,200 m / 1,609.344 ≈ 1,085.038 miles
        assert result.distance_miles == pytest.approx(1085.038, rel=1e-3)
        assert result.duration_s == pytest.approx(58320.0)
        assert len(result.waypoints) == 2

        # Assert exactly one HTTP call was made
        mock_http.assert_called_once()

    @patch("apps.routing.service._http_get_with_retry")
    def test_route_caching_and_float_rounding(self, mock_http):
        """
        Subsequent calls with identical or trivially noisy coordinates within
        4 decimal places (~11m) hit cache and make NO extra HTTP calls.
        """
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = SAMPLE_OSRM_SUCCESS
        mock_http.return_value = mock_resp

        # First call: cache miss
        result1 = get_route((41.878101, -87.629802), (29.760401, -95.369801))
        assert mock_http.call_count == 1

        # Second call with tiny float noise (within 4th decimal place)
        result2 = get_route((41.878149, -87.629849), (29.760449, -95.369849))
        assert mock_http.call_count == 1  # No second HTTP call

        assert result1.distance_miles == result2.distance_miles
        assert result1.duration_s == result2.duration_s

    @patch("apps.routing.service._http_get_with_retry")
    def test_no_route_found(self, mock_http):
        """When OSRM returns code != Ok or NoRoute, NoRouteFoundError is raised."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "code": "NoRoute",
            "message": "Impossible route between points",
        }
        mock_http.return_value = mock_resp

        with pytest.raises(NoRouteFoundError) as exc_info:
            get_route(CHICAGO, HOUSTON)

        assert "No driving route found" in str(exc_info.value)
        assert exc_info.value.code == "no_route_found"
        assert exc_info.value.status_code == 404

    @patch("apps.routing.service._http_get_with_retry")
    def test_osrm_timeout(self, mock_http):
        """When httpx times out, RoutingTimeoutError is raised (no raw exception)."""
        mock_http.side_effect = httpx.TimeoutException("Connection timed out")

        with pytest.raises(RoutingTimeoutError) as exc_info:
            get_route(CHICAGO, HOUSTON)

        assert "timed out" in str(exc_info.value)
        assert exc_info.value.code == "routing_timeout"
        assert exc_info.value.status_code == 504

    @patch("apps.routing.service._http_get_with_retry")
    def test_osrm_unreachable(self, mock_http):
        """When OSRM is unreachable, RoutingUnreachableError is raised."""
        mock_http.side_effect = httpx.ConnectError("Connection refused")

        with pytest.raises(RoutingUnreachableError) as exc_info:
            get_route(CHICAGO, HOUSTON)

        assert "unreachable" in str(exc_info.value)
        assert exc_info.value.code == "routing_unreachable"
        assert exc_info.value.status_code == 502

    def test_start_outside_conus_rejected(self):
        """Non-CONUS start coordinates are rejected early before calling OSRM."""
        with pytest.raises(LocationOutsideUSError) as exc_info:
            get_route(LONDON, HOUSTON)

        assert "outside the contiguous US" in str(exc_info.value)
        assert exc_info.value.code == "location_outside_us"

    def test_finish_outside_conus_rejected(self):
        """Non-CONUS finish coordinates (e.g. Hawaii) are rejected early."""
        with pytest.raises(LocationOutsideUSError) as exc_info:
            get_route(CHICAGO, HONOLULU)

        assert "outside the contiguous US" in str(exc_info.value)

    @patch("httpx.Client.get")
    def test_retry_bounded_at_max_one_retry(self, mock_client_get):
        """Transient error triggers at most 1 retry (2 total attempts) via tenacity."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = SAMPLE_OSRM_SUCCESS

        # First attempt fails with network error, second attempt succeeds
        mock_client_get.side_effect = [httpx.NetworkError("temporary glitch"), mock_resp]

        result = get_route(CHICAGO, HOUSTON)
        assert result.distance_miles > 0
        assert mock_client_get.call_count == 2



# ---------------------------------------------------------------------------
# Geocoding Helper Tests
# ---------------------------------------------------------------------------

class TestGeocodingHelper:
    @patch("apps.routing.geocoding.Nominatim")
    def test_successful_geocode(self, mock_nominatim_cls):
        """Resolves valid US address to (latitude, longitude) without delay."""
        mock_geolocator = MagicMock()
        mock_loc = MagicMock()
        mock_loc.latitude = 41.8781
        mock_loc.longitude = -87.6298
        mock_loc.raw = {"address": {"country_code": "us"}}
        mock_geolocator.geocode.return_value = mock_loc
        mock_nominatim_cls.return_value = mock_geolocator

        lat, lon = geocode_location("Chicago, IL")
        assert lat == pytest.approx(41.8781)
        assert lon == pytest.approx(-87.6298)
        mock_geolocator.geocode.assert_called_once()

    @patch("apps.routing.geocoding.Nominatim")
    def test_geocoding_caching(self, mock_nominatim_cls):
        """Second call for same location hits cache and makes zero Nominatim calls."""
        mock_geolocator = MagicMock()
        mock_loc = MagicMock()
        mock_loc.latitude = 29.7604
        mock_loc.longitude = -95.3698
        mock_loc.raw = {"address": {"country_code": "us"}}
        mock_geolocator.geocode.return_value = mock_loc
        mock_nominatim_cls.return_value = mock_geolocator

        # First call — cache miss
        loc1 = geocode_location("Houston, TX")
        assert mock_geolocator.geocode.call_count == 1

        # Second call — cache hit
        loc2 = geocode_location("Houston, TX")
        assert mock_geolocator.geocode.call_count == 1  # No additional call
        assert loc1 == loc2

    @patch("apps.routing.geocoding.Nominatim")
    def test_ambiguous_or_no_match(self, mock_nominatim_cls):
        """Unresolvable location raises LocationNotFoundError."""
        mock_geolocator = MagicMock()
        mock_geolocator.geocode.return_value = None
        mock_nominatim_cls.return_value = mock_geolocator

        with pytest.raises(LocationNotFoundError) as exc_info:
            geocode_location("NonExistentPlaceXYZ123")

        assert "Could not resolve location" in str(exc_info.value)
        assert exc_info.value.code == "location_not_found"
        assert exc_info.value.status_code == 400

    @patch("apps.routing.geocoding.Nominatim")
    def test_geocoding_outside_us(self, mock_nominatim_cls):
        """Locations outside US (e.g. Toronto, Canada) raise LocationOutsideUSError via country_code."""
        mock_geolocator = MagicMock()
        mock_loc = MagicMock()
        mock_loc.latitude = 43.6532
        mock_loc.longitude = -79.3832
        mock_loc.raw = {"address": {"country_code": "ca"}}
        mock_geolocator.geocode.return_value = mock_loc
        mock_nominatim_cls.return_value = mock_geolocator

        with pytest.raises(LocationOutsideUSError) as exc_info:
            geocode_location("Toronto, ON, Canada")

        assert "outside the United States" in str(exc_info.value)
        assert exc_info.value.code == "location_outside_us"

    @patch("apps.routing.geocoding.Nominatim")
    def test_geocoding_outside_conus_coordinates(self, mock_nominatim_cls):
        """US locations outside CONUS (e.g. Honolulu, HI) raise LocationOutsideUSError via coordinate bounds."""
        mock_geolocator = MagicMock()
        mock_loc = MagicMock()
        mock_loc.latitude = 21.3069
        mock_loc.longitude = -157.8583
        mock_loc.raw = {"address": {"country_code": "us"}}
        mock_geolocator.geocode.return_value = mock_loc
        mock_nominatim_cls.return_value = mock_geolocator

        with pytest.raises(LocationOutsideUSError) as exc_info:
            geocode_location("Honolulu, HI")

        assert "outside the contiguous US" in str(exc_info.value)
        assert exc_info.value.code == "location_outside_us"


    @patch("apps.routing.geocoding.Nominatim")
    def test_geocoding_service_error(self, mock_nominatim_cls):
        """Nominatim network/service errors are wrapped in GeocodingError."""
        mock_geolocator = MagicMock()
        mock_geolocator.geocode.side_effect = Exception("Service unavailable")
        mock_nominatim_cls.return_value = mock_geolocator

        with pytest.raises(GeocodingError) as exc_info:
            geocode_location("Dallas, TX")

        assert "Geocoding service error" in str(exc_info.value)
        assert exc_info.value.code == "geocoding_error"

    @patch("time.sleep")
    @patch("apps.routing.geocoding.Nominatim")
    def test_live_geocode_has_zero_artificial_delay(self, mock_nominatim_cls, mock_sleep):
        """Ad-hoc live geocoding has default delay=0.0 and never calls time.sleep."""
        mock_geolocator = MagicMock()
        mock_loc = MagicMock()
        mock_loc.latitude = 39.7392
        mock_loc.longitude = -104.9903
        mock_loc.raw = {"address": {"country_code": "us"}}
        mock_geolocator.geocode.return_value = mock_loc
        mock_nominatim_cls.return_value = mock_geolocator

        geocode_location("Denver, CO")
        mock_sleep.assert_not_called()
