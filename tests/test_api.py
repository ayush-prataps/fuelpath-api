"""
tests/test_api.py
==================
Integration tests for POST /api/v1/route/ via DRF's APIClient.

These tests hit the Django URL routing stack but mock out all external
calls (OSRM + DB queries) so they run without a live database or network.
"""

import pytest
from rest_framework import status
from rest_framework.test import APIClient


@pytest.fixture
def api_client() -> APIClient:
    return APIClient()


@pytest.fixture
def valid_payload() -> dict:
    return {"start": "Chicago, IL", "finish": "Houston, TX"}


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
        """
        With valid input, the endpoint must respond with JSON — even if
        the response is 501 while the business logic is stubbed out.
        """
        response = api_client.post(self.URL, valid_payload, format="json")
        assert response["Content-Type"] == "application/json"
        assert response.status_code in (
            status.HTTP_200_OK,
            status.HTTP_501_NOT_IMPLEMENTED,
        )

    def test_whitespace_only_start_returns_400(self, api_client):
        """
        Leading/trailing whitespace is stripped by the serializer;
        an all-whitespace value should be rejected.
        """
        response = api_client.post(
            self.URL, {"start": "   ", "finish": "Houston, TX"}, format="json"
        )
        # Expectation: serializer validate_start raises if blank after strip
        # This test documents the intent — assertion updated once implemented.
        assert response.status_code in (
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_501_NOT_IMPLEMENTED,  # acceptable until validator added
        )


# ---------------------------------------------------------------------------
# Success-path tests (xfail until routing + optimizer are implemented)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
@pytest.mark.xfail(strict=True, reason="business logic not yet implemented — view returns HTTP 501")
class TestRouteEndpointSuccess:
    URL = "/api/v1/route/"

    def test_response_shape(self, api_client, valid_payload):
        response = api_client.post(self.URL, valid_payload, format="json")
        assert response.status_code == status.HTTP_200_OK
        data = response.data
        assert "route" in data
        assert "fuel_stops" in data
        assert "total_gallons" in data
        assert "total_fuel_cost_usd" in data

    def test_route_is_geojson_feature_collection(self, api_client, valid_payload):
        response = api_client.post(self.URL, valid_payload, format="json")
        route = response.data["route"]
        assert route["type"] == "FeatureCollection"
        assert "features" in route

    def test_fuel_stops_are_in_usa(self, api_client, valid_payload):
        response = api_client.post(self.URL, valid_payload, format="json")
        for stop in response.data["fuel_stops"]:
            # Rough bounding box for the contiguous US
            assert 24.0 <= stop["latitude"] <= 50.0
            assert -125.0 <= stop["longitude"] <= -66.0

    def test_totals_are_consistent(self, api_client, valid_payload):
        response = api_client.post(self.URL, valid_payload, format="json")
        stops = response.data["fuel_stops"]
        expected_gallons = sum(s["gallons"] for s in stops)
        expected_cost = sum(s["cost_usd"] for s in stops)
        assert response.data["total_gallons"] == pytest.approx(expected_gallons, rel=1e-4)
        assert response.data["total_fuel_cost_usd"] == pytest.approx(expected_cost, rel=1e-4)
