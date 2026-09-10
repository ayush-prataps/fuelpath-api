"""
DRF views for the fuel-path API.

RouteView — POST /api/v1/route/
    Accepts start + finish locations, orchestrates the routing and
    optimisation services, and returns the full response payload.

All heavy lifting is delegated to:
    apps.routing.service   — OSRM client
    apps.optimizer.solver  — DP fuel-stop selection
"""

import logging

from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import RouteRequestSerializer

logger = logging.getLogger(__name__)


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
            "route":            { ...GeoJSON FeatureCollection... },
            "fuel_stops":       [ { ...FuelStop... }, ... ],
            "total_gallons":    float,
            "total_fuel_cost_usd": float
        }
    """

    def post(self, request: Request) -> Response:
        serializer = RouteRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        start: str = serializer.validated_data["start"]
        finish: str = serializer.validated_data["finish"]

        logger.info("Route request: %r → %r", start, finish)

        # TODO: geocode start/finish → (lon, lat) pairs
        # TODO: call apps.routing.service.get_route(origin, destination)
        # TODO: call apps.optimizer.solver.optimise(route, stations, vehicle_config)
        # TODO: assemble and return the response payload

        return Response(
            {
                "detail": "Not yet implemented.",
                "start": start,
                "finish": finish,
            },
            status=status.HTTP_501_NOT_IMPLEMENTED,
        )
