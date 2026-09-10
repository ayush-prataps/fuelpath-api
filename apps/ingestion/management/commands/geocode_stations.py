"""
Management command: geocode_stations
======================================
Runs the offline geocoding pass for FuelStation rows that have no
coordinates yet (latitude IS NULL).

Usage:
    python manage.py geocode_stations [--limit 100] [--delay 1.0]

Uses geopy + Nominatim (respects Nominatim's 1 req/sec policy by default).
Each geocoded station is saved immediately so the command is resumable.

For bulk geocoding (thousands of stations) consider the Census Batch
Geocoder or a commercial provider — set that up in a subclass.
"""

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Geocode FuelStation rows that are missing coordinates."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--limit",
            dest="limit",
            type=int,
            default=0,
            help="Max rows to geocode in one run (0 = no limit).",
        )
        parser.add_argument(
            "--delay",
            dest="delay",
            type=float,
            default=1.1,
            help="Seconds to wait between Nominatim requests (default: 1.1).",
        )

    def handle(self, *args, **options) -> None:
        limit: int = options["limit"]
        delay: float = options["delay"]

        self.stdout.write(
            f"Geocoding stations (limit={limit or 'none'}, delay={delay}s)"
        )

        # TODO: query FuelStation.objects.filter(latitude__isnull=True)
        # TODO: iterate, call geopy.geocoders.Nominatim, save, sleep(delay)
        raise NotImplementedError(
            "geocode_stations is not yet implemented. "
            "Implement geopy calls and model updates here."
        )
