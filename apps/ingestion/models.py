"""
Ingestion models — FuelStation.

One row per unique (opis_id) entry from the source CSV.
Coordinates are populated by the load_fuel_stations command during
the Nominatim geocoding pass; they are nullable until that step completes.
"""

from django.db import models


class FuelStation(models.Model):
    """
    A US truck-stop fuel station loaded from the OPIS price CSV.

    ``opis_id`` is the natural business key used for idempotent upserts.
    The same physical station can appear with multiple ``name`` values and
    ``rack_id`` values across CSV snapshots; we store the most-recently-seen
    values and keep one canonical row per opis_id.

    Coordinates are set to the city-centroid returned by Nominatim during
    ingestion and are left NULL when geocoding fails for a city/state pair.
    """

    opis_id = models.IntegerField(
        unique=True,
        db_index=True,
        help_text="OPIS Truckstop ID — primary business key from the source CSV.",
    )
    name = models.CharField(max_length=255, help_text="Truckstop name.")
    address = models.CharField(
        max_length=512,
        help_text="Raw address string as it appears in the CSV.",
    )
    city = models.CharField(max_length=100)
    state = models.CharField(
        max_length=2,
        help_text="Two-character US state code.",
    )
    rack_id = models.IntegerField(
        help_text="Rack ID from the CSV — a pricing zone identifier.",
    )
    price = models.DecimalField(
        max_digits=10,
        decimal_places=8,
        help_text="Retail fuel price in USD per gallon.",
    )

    # Coordinates — set at ingestion time via Nominatim city-level geocoding.
    latitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        null=True,
        blank=True,
        help_text="WGS-84 latitude (city centroid from Nominatim).",
    )
    longitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        null=True,
        blank=True,
        help_text="WGS-84 longitude (city centroid from Nominatim).",
    )
    geocoded_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp of the last successful geocoding for this station.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = "ingestion"
        indexes = [
            models.Index(fields=["city", "state"], name="idx_station_city_state"),
            models.Index(fields=["latitude", "longitude"], name="idx_station_coords"),
        ]
        verbose_name = "Fuel Station"
        verbose_name_plural = "Fuel Stations"

    def __str__(self) -> str:
        return f"{self.name} ({self.city}, {self.state})"
