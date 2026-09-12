"""
DRF views for the fuel-path API.

RouteView — POST /api/v1/route/
    Accepts start + finish locations, orchestrates the routing and
    optimisation services, and returns the full response payload.

Pipeline per request:
    1. Validate request body (RouteRequestSerializer).
    2. Geocode start + finish via apps.routing.geocoding.geocode_location.
    3. Fetch route from OSRM via apps.routing.service.get_route.
    4. Load FuelStation rows (non-null coords only) and match to route
       corridor via apps.optimizer.spatial.match_stations_to_route.
    5. Run DP solver via apps.optimizer.solver.optimise.
    6. Serialize and return structured JSON response.

Error mapping (no raw exceptions reach the client):
    LocationNotFoundError    → 400  location_not_found
    LocationOutsideUSError   → 400  location_outside_us
    GeocodingError           → 502  geocoding_error
    NoRouteFoundError        → 404  no_route_found
    RoutingTimeoutError      → 504  routing_timeout
    RoutingUnreachableError  → 502  routing_unreachable
    InfeasibleRouteError     → 422  infeasible_route
    Unexpected               → 500  (logged, re-raised to Django 500)
"""

from __future__ import annotations

import logging

from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.ingestion.models import FuelStation
from apps.optimizer.solver import InfeasibleRouteError, optimise
from apps.optimizer.spatial import match_stations_to_route
from apps.optimizer.types import Station, VehicleConfig
from apps.routing.exceptions import (
    GeocodingError,
    LocationNotFoundError,
    LocationOutsideUSError,
    NoRouteFoundError,
    RoutingServiceError,
    RoutingTimeoutError,
    RoutingUnreachableError,
)
from apps.routing.geocoding import geocode_location
from apps.routing.service import Coordinate, get_route

from .serializers import RouteRequestSerializer

logger = logging.getLogger(__name__)

_MILES_TO_M = 1_609.344


def _error_response(code: str, message: str, http_status: int) -> Response:
    """Produce a consistent error envelope."""
    return Response(
        {"error": {"code": code, "message": message}},
        status=http_status,
    )


class RouteView(APIView):
    """
    POST /api/v1/route/

    Request body:
        {
            "start":  "Chicago, IL",
            "finish": "Houston, TX"
        }

    Response (HTTP 200):
        {
            "route": {
                "distance_miles": float,      # rounded to 1 decimal
                "duration_minutes": float,    # rounded to 1 decimal
                "geometry": { ...GeoJSON FeatureCollection... }
            },
            "fuel": {
                "mpg": float,
                "max_range_miles": float,
                "total_gallons": float,       # rounded to 2 decimals
                "total_cost_usd": float       # rounded to 2 decimals
            },
            "fuel_stops": [
                {
                    "sequence": int,
                    "station_name": str,
                    "city": str,
                    "state": str,
                    "latitude": float,
                    "longitude": float,
                    "distance_from_start_miles": float,   # rounded to 1 decimal
                    "price_per_gallon": float,
                    "gallons_purchased": float,           # rounded to 2 decimals
                    "cost_usd": float                     # rounded to 2 decimals
                },
                ...
            ]
        }
    """

    def post(self, request: Request) -> Response:
        # ------------------------------------------------------------------ #
        # 1. Validate request body
        # ------------------------------------------------------------------ #
        serializer = RouteRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        start_str: str = serializer.validated_data["start"]
        finish_str: str = serializer.validated_data["finish"]

        logger.info("Route request: %r → %r", start_str, finish_str)

        # ------------------------------------------------------------------ #
        # 2. Geocode start and finish
        # ------------------------------------------------------------------ #
        try:
            start_lat, start_lon = geocode_location(start_str)
            finish_lat, finish_lon = geocode_location(finish_str)
        except LocationNotFoundError as exc:
            return _error_response("location_not_found", str(exc), status.HTTP_400_BAD_REQUEST)
        except LocationOutsideUSError as exc:
            return _error_response("location_outside_us", str(exc), status.HTTP_400_BAD_REQUEST)
        except GeocodingError as exc:
            logger.warning("Geocoding service error: %s", exc)
            return _error_response("geocoding_error", str(exc), status.HTTP_502_BAD_GATEWAY)

        start_coord = Coordinate(latitude=start_lat, longitude=start_lon)
        finish_coord = Coordinate(latitude=finish_lat, longitude=finish_lon)

        # ------------------------------------------------------------------ #
        # 3. Fetch route from OSRM
        # ------------------------------------------------------------------ #
        try:
            route_result = get_route(start_coord, finish_coord)
        except NoRouteFoundError as exc:
            return _error_response("no_route_found", str(exc), status.HTTP_404_NOT_FOUND)
        except RoutingTimeoutError as exc:
            return _error_response("routing_timeout", str(exc), status.HTTP_504_GATEWAY_TIMEOUT)
        except RoutingUnreachableError as exc:
            logger.warning("OSRM unreachable: %s", exc)
            return _error_response("routing_unreachable", str(exc), status.HTTP_502_BAD_GATEWAY)
        except RoutingServiceError as exc:
            logger.exception("Unexpected routing service error")
            return _error_response(
                getattr(exc, "code", "routing_error"),
                str(exc),
                getattr(exc, "status_code", status.HTTP_500_INTERNAL_SERVER_ERROR),
            )

        # ------------------------------------------------------------------ #
        # 4. Load candidate stations (exclude NULL-coord rows) and match
        # ------------------------------------------------------------------ #
        db_stations = FuelStation.objects.filter(
            latitude__isnull=False,
            longitude__isnull=False,
        ).only("id", "name", "city", "state", "latitude", "longitude", "price")

        optimizer_stations: list[Station] = [
            Station(
                id=fs.id,
                name=fs.name,
                city=fs.city,
                state=fs.state,
                latitude=float(fs.latitude),
                longitude=float(fs.longitude),
                retail_price_usd=float(fs.price),
            )
            for fs in db_stations
        ]

        # Extract (lon, lat) coordinate list from route_result.geojson LineString geometry
        features = route_result.geojson.get("features", [])
        if features and "geometry" in features[0]:
            coords_raw = features[0]["geometry"].get("coordinates", [])
        elif "geometry" in route_result.geojson:
            coords_raw = route_result.geojson["geometry"].get("coordinates", [])
        elif "coordinates" in route_result.geojson:
            coords_raw = route_result.geojson.get("coordinates", [])
        else:
            coords_raw = []

        route_coords = [
            (float(c[0]), float(c[1]))
            for c in coords_raw
            if isinstance(c, (list, tuple)) and len(c) >= 2
        ]

        matched_stations = match_stations_to_route(
            route_coords=route_coords,
            stations=optimizer_stations,
        )

        # ------------------------------------------------------------------ #
        # 5. Run DP solver
        # ------------------------------------------------------------------ #
        vehicle = VehicleConfig(
            range_miles=500,   # read from env in a future iteration
            mpg=10,
        )

        try:
            opt_result = optimise(
                route_distance_m=route_result.distance_m,
                stations=matched_stations,
                vehicle=vehicle,
            )
        except InfeasibleRouteError as exc:
            return _error_response(
                "infeasible_route",
                (
                    f"No feasible fueling plan exists for this route: {exc}. "
                    "There may be a gap between stations that exceeds the vehicle's "
                    f"{vehicle.range_miles:.0f}-mile range."
                ),
                status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        # ------------------------------------------------------------------ #
        # 6. Build response
        # ------------------------------------------------------------------ #
        fuel_stops_payload = []
        for seq, stop in enumerate(opt_result.fuel_stops, start=1):
            s = stop.station
            fuel_stops_payload.append({
                "sequence": seq,
                "station_name": s.name,
                "city": s.city,
                "state": s.state,
                "latitude": s.latitude,
                "longitude": s.longitude,
                "distance_from_start_miles": round(s.distance_along_route_miles, 1),
                "price_per_gallon": float(s.retail_price_usd),
                "gallons_purchased": round(stop.gallons, 2),
                "cost_usd": round(stop.cost_usd, 2),
            })

        response_data = {
            "route": {
                "distance_miles": round(route_result.distance_miles, 1),
                "duration_minutes": round(route_result.duration_s / 60, 1),
                "geometry": route_result.geojson,
            },
            "fuel": {
                "mpg": vehicle.mpg,
                "max_range_miles": vehicle.range_miles,
                "total_gallons": round(opt_result.total_gallons, 2),
                "total_cost_usd": round(opt_result.total_fuel_cost_usd, 2),
            },
            "fuel_stops": fuel_stops_payload,
        }

        logger.info(
            "Route %r→%r: %.1f mi, %d stop(s), $%.2f",
            start_str,
            finish_str,
            route_result.distance_miles,
            len(fuel_stops_payload),
            opt_result.total_fuel_cost_usd,
        )

        return Response(response_data, status=status.HTTP_200_OK)
