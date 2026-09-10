"""
Management command: load_stations
==================================
Loads the fuel-price CSV into the FuelStation table.

Usage:
    python manage.py load_stations --csv data/fuel-prices.csv [--batch 500]

Expected CSV columns (case-insensitive):
    TRUCKSTOP NAME, ADDRESS, CITY, STATE, RACK ID, RETAIL PRICE

Idempotent: uses RACK_ID as the natural dedup key; existing rows are
updated if the price has changed.
"""

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Load fuel-station data from a CSV file into the database."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--csv",
            dest="csv_path",
            required=True,
            help="Path to the fuel-price CSV file.",
        )
        parser.add_argument(
            "--batch",
            dest="batch_size",
            type=int,
            default=500,
            help="Number of rows to bulk-insert per DB round-trip (default: 500).",
        )

    def handle(self, *args, **options) -> None:
        csv_path: str = options["csv_path"]
        batch_size: int = options["batch_size"]

        self.stdout.write(f"Loading stations from: {csv_path}")

        # TODO: open CSV, validate columns, bulk_create / update_or_create
        raise NotImplementedError(
            "load_stations is not yet implemented. "
            "Implement CSV parsing and bulk_create logic here."
        )
