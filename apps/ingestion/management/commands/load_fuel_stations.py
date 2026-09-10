"""
Management command: load_fuel_stations
========================================
Loads (or refreshes) FuelStation rows from the OPIS price CSV, geocoding
each unique (city, state) pair via Nominatim.

Usage
-----
    python manage.py load_fuel_stations \\
        [--csv data/raw/fuel_prices.csv]   # default path \\
        [--geocache data/cache/geocode_cache.json]  # default path \\
        [--delay 1.1]                      # seconds between Nominatim calls \\
        [--dry-run]                        # parse + geocode only, no DB writes

Algorithm
---------
1.  Read all rows from the CSV.
2.  Filter out Canadian provinces (AB, BC, MB, NB, NS, ON, QC, SK, YT).
3.  Build the set of unique (city, state) pairs.
4.  Load the geocode cache from disk (JSON keyed by "city,state").
5.  For each uncached pair, call Nominatim, store the result, sleep(delay).
6.  Persist the updated cache to disk.
7.  Bulk-upsert FuelStation rows (idempotent on opis_id).
8.  Log a summary.

Idempotency
-----------
Rows are upserted using update_or_create keyed on ``opis_id``.
Running the command twice is safe — existing rows are updated in place.
"""

from __future__ import annotations

import csv
import json
import logging
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from geopy.geocoders import Nominatim

logger = logging.getLogger(__name__)

# Canadian province codes present in the source data — filtered out.
CANADIAN_PROVINCES: frozenset[str] = frozenset(
    {"AB", "BC", "MB", "NB", "NS", "ON", "QC", "SK", "YT"}
)

# Expected CSV header names (lowercased, stripped) → model field mapping.
CSV_COLUMNS = {
    "opis truckstop id": "opis_id",
    "truckstop name": "name",
    "address": "address",
    "city": "city",
    "state": "state",
    "rack id": "rack_id",
    "retail price": "price",
}


class Command(BaseCommand):
    help = "Load fuel-station data from the OPIS CSV into the database."

    # ------------------------------------------------------------------
    # Argument parsing
    # ------------------------------------------------------------------

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--csv",
            dest="csv_path",
            default="data/raw/fuel_prices.csv",
            help="Path to the fuel-price CSV (default: data/raw/fuel_prices.csv).",
        )
        parser.add_argument(
            "--geocache",
            dest="geocache_path",
            default="data/cache/geocode_cache.json",
            help="Path to the geocode cache JSON (default: data/cache/geocode_cache.json).",
        )
        parser.add_argument(
            "--delay",
            dest="delay",
            type=float,
            default=1.1,
            help="Seconds to wait between Nominatim requests (default: 1.1).",
        )
        parser.add_argument(
            "--dry-run",
            dest="dry_run",
            action="store_true",
            default=False,
            help="Parse and geocode but do not write to the database.",
        )

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def handle(self, *args, **options) -> None:
        csv_path = Path(options["csv_path"])
        geocache_path = Path(options["geocache_path"])
        delay: float = options["delay"]
        dry_run: bool = options["dry_run"]

        if not csv_path.exists():
            raise CommandError(f"CSV file not found: {csv_path}")

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN — no database writes."))

        # ── 1. Parse CSV ───────────────────────────────────────────────
        self.stdout.write(f"Reading: {csv_path}")
        rows, skipped_canadian = self._parse_csv(csv_path)
        self.stdout.write(
            f"  Parsed {len(rows) + skipped_canadian} rows → "
            f"{len(rows)} US, {skipped_canadian} Canadian (filtered)."
        )

        # ── 2. Unique (city, state) pairs ──────────────────────────────
        city_state_pairs: set[tuple[str, str]] = {
            (r["city"], r["state"]) for r in rows
        }
        self.stdout.write(
            f"  Unique city/state pairs to geocode: {len(city_state_pairs)}"
        )

        # ── 3. Geocode ─────────────────────────────────────────────────
        geocache_path.parent.mkdir(parents=True, exist_ok=True)
        geocode_cache = self._load_geocache(geocache_path)

        geocode_cache, geocode_stats = self._geocode_pairs(
            city_state_pairs, geocode_cache, delay
        )
        self._save_geocache(geocache_path, geocode_cache)

        self.stdout.write(
            f"  Geocoding: {geocode_stats['hits']} cache hits, "
            f"{geocode_stats['new']} new lookups, "
            f"{geocode_stats['failed']} failed."
        )
        if geocode_stats["failed_pairs"]:
            self.stdout.write(
                self.style.WARNING(
                    "  Failed pairs (no coords will be stored): "
                    + ", ".join(
                        f"{c}/{s}" for c, s in sorted(geocode_stats["failed_pairs"])
                    )
                )
            )

        # ── 4. Deduplicate by opis_id (last-wins, matches CSV order) ──────
        #
        # 568 OPIS IDs appear more than once in the source CSV (1,473 extra
        # rows) — same station, different name variants (e.g. "PILOT #1243"
        # vs "PILOT TRAVEL CENTER #1243").  We collapse them here so we make
        # exactly len(deduped) DB round-trips rather than len(rows).
        deduped: dict[int, dict] = {r["opis_id"]: r for r in rows}  # last-wins
        collapsed = len(rows) - len(deduped)
        if collapsed:
            self.stdout.write(
                f"  Collapsed {collapsed} duplicate opis_id rows "
                f"({len(rows)} → {len(deduped)} unique stations)."
            )

        # ── 5. Upsert ──────────────────────────────────────────────────
        if dry_run:
            self.stdout.write("DRY RUN complete — skipping DB writes.")
            return

        created, updated = self._upsert_stations(list(deduped.values()), geocode_cache)
        self.stdout.write(
            self.style.SUCCESS(
                f"\nDone. {created} stations created, {updated} updated. "
                f"({skipped_canadian} Canadian rows excluded, "
                f"{collapsed} duplicate opis_id rows collapsed)"
            )
        )

    # ------------------------------------------------------------------
    # CSV parsing
    # ------------------------------------------------------------------

    def _parse_csv(self, csv_path: Path) -> tuple[list[dict], int]:
        """
        Parse the CSV and return (us_rows, canadian_count).

        Each row in us_rows is a dict with keys matching CSV_COLUMNS values.
        Rows with invalid/missing opis_id or price are skipped with a warning.
        """
        us_rows: list[dict] = []
        skipped_canadian = 0
        skipped_invalid = 0

        with csv_path.open(newline="", encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh)

            # Normalise header names: strip whitespace + lowercase
            reader.fieldnames = [f.strip().lower() for f in (reader.fieldnames or [])]

            missing = set(CSV_COLUMNS.keys()) - set(reader.fieldnames)
            if missing:
                raise CommandError(
                    f"CSV is missing expected columns: {missing}. "
                    f"Found: {reader.fieldnames}"
                )

            for lineno, raw in enumerate(reader, start=2):
                state = raw.get("state", "").strip().upper()

                if state in CANADIAN_PROVINCES:
                    skipped_canadian += 1
                    continue

                try:
                    row = self._coerce_row(raw)
                except ValueError as exc:
                    skipped_invalid += 1
                    logger.debug("Line %d skipped: %s", lineno, exc)
                    continue

                us_rows.append(row)

        if skipped_invalid:
            self.stdout.write(
                self.style.WARNING(
                    f"  {skipped_invalid} rows skipped due to invalid data "
                    f"(see debug log for details)."
                )
            )

        return us_rows, skipped_canadian

    @staticmethod
    def _coerce_row(raw: dict[str, str]) -> dict[str, Any]:
        """Convert raw CSV strings to Python types; raise ValueError on bad data."""
        try:
            opis_id = int(raw["opis truckstop id"].strip())
        except (ValueError, KeyError) as exc:
            raise ValueError(f"Invalid opis_id: {raw.get('opis truckstop id')!r}") from exc

        try:
            rack_id = int(raw["rack id"].strip())
        except (ValueError, KeyError) as exc:
            raise ValueError(f"Invalid rack_id: {raw.get('rack id')!r}") from exc

        try:
            price = Decimal(raw["retail price"].strip())
        except (InvalidOperation, KeyError) as exc:
            raise ValueError(f"Invalid price: {raw.get('retail price')!r}") from exc

        return {
            "opis_id": opis_id,
            "name": raw["truckstop name"].strip(),
            "address": raw["address"].strip(),
            "city": raw["city"].strip(),
            "state": raw["state"].strip().upper(),
            "rack_id": rack_id,
            "price": price,
        }

    # ------------------------------------------------------------------
    # Geocoding
    # ------------------------------------------------------------------

    @staticmethod
    def _load_geocache(path: Path) -> dict[str, dict | None]:
        """Load the on-disk JSON geocode cache. Returns {} if not found."""
        if path.exists():
            with path.open(encoding="utf-8") as fh:
                return json.load(fh)
        return {}

    @staticmethod
    def _save_geocache(path: Path, cache: dict) -> None:
        with path.open("w", encoding="utf-8") as fh:
            json.dump(cache, fh, indent=2, sort_keys=True)

    def _geocode_pairs(
        self,
        pairs: set[tuple[str, str]],
        cache: dict[str, dict | None],
        delay: float,
    ) -> tuple[dict, dict]:
        """
        Geocode all pairs not already in the cache via Nominatim.

        Returns (updated_cache, stats_dict).
        stats_dict keys: hits, new, failed, failed_pairs.
        """
        user_agent = getattr(
            settings, "GEOCODER_USER_AGENT", "fuelpath-ingestion/1.0"
        )
        geolocator = Nominatim(user_agent=user_agent)

        stats: dict[str, Any] = {
            "hits": 0,
            "new": 0,
            "failed": 0,
            "failed_pairs": [],
        }

        uncached = [p for p in sorted(pairs) if f"{p[0]},{p[1]}" not in cache]
        stats["hits"] = len(pairs) - len(uncached)

        for city, state in uncached:
            key = f"{city},{state}"
            query = f"{city}, {state}, USA"
            try:
                location = geolocator.geocode(query, timeout=10)
                if location:
                    cache[key] = {
                        "latitude": location.latitude,
                        "longitude": location.longitude,
                    }
                    stats["new"] += 1
                    logger.debug("Geocoded %s → (%.5f, %.5f)", key, location.latitude, location.longitude)
                else:
                    cache[key] = None  # Mark as attempted but not found
                    stats["failed"] += 1
                    stats["failed_pairs"].append((city, state))
                    logger.warning("Nominatim returned no result for: %s", query)
            except Exception as exc:  # noqa: BLE001
                # Don't abort the whole command on a single geocoding failure
                cache[key] = None
                stats["failed"] += 1
                stats["failed_pairs"].append((city, state))
                logger.warning("Geocoding error for %s: %s", query, exc)

            time.sleep(delay)

        return cache, stats

    # ------------------------------------------------------------------
    # Database upsert
    # ------------------------------------------------------------------

    def _upsert_stations(
        self,
        rows: list[dict],
        geocode_cache: dict[str, dict | None],
    ) -> tuple[int, int]:
        """
        Upsert ``rows`` into FuelStation, keyed on opis_id.

        Caller is responsible for deduplicating by opis_id before this call
        (see handle()); each row here triggers exactly one DB round-trip.

        Returns (created_count, updated_count).
        """
        from apps.ingestion.models import FuelStation

        created = updated = 0
        now = timezone.now()

        for row in rows:
            key = f"{row['city']},{row['state']}"
            coords = geocode_cache.get(key)

            defaults = {
                "name": row["name"],
                "address": row["address"],
                "city": row["city"],
                "state": row["state"],
                "rack_id": row["rack_id"],
                "price": row["price"],
                "latitude": Decimal(str(coords["latitude"])) if coords else None,
                "longitude": Decimal(str(coords["longitude"])) if coords else None,
                "geocoded_at": now if coords else None,
            }

            _, was_created = FuelStation.objects.update_or_create(
                opis_id=row["opis_id"],
                defaults=defaults,
            )
            if was_created:
                created += 1
            else:
                updated += 1

        return created, updated
