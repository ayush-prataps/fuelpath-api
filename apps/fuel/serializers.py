"""
DRF serializers for the fuel-path API.

RouteRequestSerializer  — validates the inbound POST body.
FuelStopSerializer      — represents a single optimised fuel stop.
RouteResponseSerializer — shapes the full API response (informational only;
                           not used for deserialization).
"""

from rest_framework import serializers


class RouteRequestSerializer(serializers.Serializer):
    """Validates the body of POST /api/v1/route/."""

    start = serializers.CharField(
        help_text="Start location — free-text US address or 'City, ST'.",
        max_length=256,
    )
    finish = serializers.CharField(
        help_text="Destination — free-text US address or 'City, ST'.",
        max_length=256,
    )

    def validate_start(self, value: str) -> str:
        return value.strip()

    def validate_finish(self, value: str) -> str:
        return value.strip()


class FuelStopSerializer(serializers.Serializer):
    """A single optimised fuel stop in the response."""

    name = serializers.CharField()
    city = serializers.CharField()
    state = serializers.CharField(max_length=2)
    latitude = serializers.FloatField()
    longitude = serializers.FloatField()
    retail_price_usd = serializers.FloatField()
    gallons = serializers.FloatField(
        help_text="Gallons to purchase at this stop."
    )
    cost_usd = serializers.FloatField(
        help_text="Cost of fuel purchased at this stop."
    )


class RouteResponseSerializer(serializers.Serializer):
    """Shape of the successful API response (documentation only)."""

    route = serializers.DictField(
        child=serializers.JSONField(),
        help_text="GeoJSON FeatureCollection containing the driving route.",
    )
    fuel_stops = FuelStopSerializer(many=True)
    total_gallons = serializers.FloatField()
    total_fuel_cost_usd = serializers.FloatField()
