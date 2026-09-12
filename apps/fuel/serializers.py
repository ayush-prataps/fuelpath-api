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
        stripped = value.strip()
        if not stripped:
            raise serializers.ValidationError("start must be a non-empty location string.")
        return stripped

    def validate_finish(self, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise serializers.ValidationError("finish must be a non-empty location string.")
        return stripped


class FuelStopSerializer(serializers.Serializer):
    """A single optimised fuel stop in the response."""

    sequence = serializers.IntegerField(help_text="1-based stop number along the route.")
    station_name = serializers.CharField()
    city = serializers.CharField()
    state = serializers.CharField(max_length=2)
    latitude = serializers.FloatField()
    longitude = serializers.FloatField()
    distance_from_start_miles = serializers.FloatField()
    price_per_gallon = serializers.FloatField()
    gallons_purchased = serializers.FloatField()
    cost_usd = serializers.FloatField()


class RouteResponseSerializer(serializers.Serializer):
    """Shape of the successful API response (documentation only)."""

    route = serializers.DictField(
        help_text="Driving route summary and GeoJSON geometry.",
    )
    fuel = serializers.DictField(
        help_text="Vehicle config and fuel totals.",
    )
    fuel_stops = FuelStopSerializer(many=True)
