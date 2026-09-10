"""
tests/test_ingestion.py
=======================
Tests for the load_fuel_stations management command.

Strategy
--------
* The command is called via Django's `call_command()` helper so it goes
  through the full argument/option stack.
* Nominatim calls are patched with `unittest.mock.patch` — tests must
  never make real network calls.
* The fixture CSV at tests/fixtures/fuel_prices_sample.csv contains 4 US
  rows and 3 Canadian rows, giving us a controlled input to assert against.

Fixture CSV contents:
  US  — opis_id 7  (Big Cabin, OK)
  US  — opis_id 9  (Tomah, WI)
  US  — opis_id 20 (Gila Bend, AZ)
  US  — opis_id 42 (Amarillo, TX)
  CA  — opis_id 629 (Edmonton, AB)  ← must be excluded
  CA  — opis_id 700 (Toronto, ON)   ← must be excluded
  CA  — opis_id 750 (Montreal, QC)  ← must be excluded
"""

from __future__ import annotations

import json
import tempfile
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from django.core.management import call_command

from apps.ingestion.models import FuelStation

FIXTURE_CSV = Path(__file__).parent / "fixtures" / "fuel_prices_sample.csv"

# Coordinates returned by the mock geocoder for each city
MOCK_COORDS = {
    "Big Cabin, OK, USA": (36.5369, -95.2183),
    "Tomah, WI, USA": (43.9741, -90.5040),
    "Gila Bend, AZ, USA": (32.9478, -112.7183),
    "Amarillo, TX, USA": (35.2220, -101.8313),
}


def _make_mock_geolocator():
    """Return a Nominatim mock whose geocode() returns plausible locations."""

    def geocode(query: str, timeout=10):  # noqa: ARG001
        coords = MOCK_COORDS.get(query)
        if coords is None:
            return None
        loc = MagicMock()
        loc.latitude, loc.longitude = coords
        return loc

    mock = MagicMock()
    mock.geocode.side_effect = geocode
    return mock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_command(csv_path: Path, geocache_path: Path, **kwargs):
    """Call load_fuel_stations with sensible test defaults (delay=0, no sleep)."""
    call_command(
        "load_fuel_stations",
        csv=str(csv_path),
        geocache=str(geocache_path),
        delay=0,  # no sleeping in tests
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestLoadFuelStationsCommand:
    @patch("apps.ingestion.management.commands.load_fuel_stations.time.sleep")
    @patch("apps.ingestion.management.commands.load_fuel_stations.Nominatim")
    def test_canadian_rows_excluded(self, mock_nominatim_cls, mock_sleep):
        """Canadian province rows must never be written to the DB."""
        mock_nominatim_cls.return_value = _make_mock_geolocator()

        with tempfile.TemporaryDirectory() as tmpdir:
            geocache = Path(tmpdir) / "geocode_cache.json"
            _run_command(FIXTURE_CSV, geocache)

        canadian_opis_ids = [629, 700, 750]
        assert not FuelStation.objects.filter(
            opis_id__in=canadian_opis_ids
        ).exists(), "Canadian stations must not be persisted."

    @patch("apps.ingestion.management.commands.load_fuel_stations.time.sleep")
    @patch("apps.ingestion.management.commands.load_fuel_stations.Nominatim")
    def test_us_rows_created(self, mock_nominatim_cls, mock_sleep):
        """All 4 US rows from the fixture must be written to the DB."""
        mock_nominatim_cls.return_value = _make_mock_geolocator()

        with tempfile.TemporaryDirectory() as tmpdir:
            geocache = Path(tmpdir) / "geocode_cache.json"
            _run_command(FIXTURE_CSV, geocache)

        assert FuelStation.objects.count() == 4
        us_opis_ids = [7, 9, 20, 42]
        assert FuelStation.objects.filter(opis_id__in=us_opis_ids).count() == 4

    @patch("apps.ingestion.management.commands.load_fuel_stations.time.sleep")
    @patch("apps.ingestion.management.commands.load_fuel_stations.Nominatim")
    def test_coordinates_attached(self, mock_nominatim_cls, mock_sleep):
        """Each US station must have lat/lon populated from the geocoder."""
        mock_nominatim_cls.return_value = _make_mock_geolocator()

        with tempfile.TemporaryDirectory() as tmpdir:
            geocache = Path(tmpdir) / "geocode_cache.json"
            _run_command(FIXTURE_CSV, geocache)

        for station in FuelStation.objects.all():
            assert station.latitude is not None, (
                f"{station} (opis_id={station.opis_id}) has no latitude"
            )
            assert station.longitude is not None, (
                f"{station} (opis_id={station.opis_id}) has no longitude"
            )

        # Spot-check Big Cabin, OK
        ok_station = FuelStation.objects.get(opis_id=7)
        assert float(ok_station.latitude) == pytest.approx(36.5369, abs=1e-3)
        assert float(ok_station.longitude) == pytest.approx(-95.2183, abs=1e-3)

    @patch("apps.ingestion.management.commands.load_fuel_stations.time.sleep")
    @patch("apps.ingestion.management.commands.load_fuel_stations.Nominatim")
    def test_idempotent_double_run(self, mock_nominatim_cls, mock_sleep):
        """Running the command twice must not duplicate rows."""
        mock_nominatim_cls.return_value = _make_mock_geolocator()

        with tempfile.TemporaryDirectory() as tmpdir:
            geocache = Path(tmpdir) / "geocode_cache.json"
            _run_command(FIXTURE_CSV, geocache)
            _run_command(FIXTURE_CSV, geocache)  # second run

        assert FuelStation.objects.count() == 4  # still 4, not 8

    @patch("apps.ingestion.management.commands.load_fuel_stations.time.sleep")
    @patch("apps.ingestion.management.commands.load_fuel_stations.Nominatim")
    def test_geocache_persisted_to_disk(self, mock_nominatim_cls, mock_sleep):
        """The geocode cache JSON file must be written after a run."""
        mock_nominatim_cls.return_value = _make_mock_geolocator()

        with tempfile.TemporaryDirectory() as tmpdir:
            geocache = Path(tmpdir) / "geocode_cache.json"
            _run_command(FIXTURE_CSV, geocache)

            assert geocache.exists(), "Geocode cache file was not written."
            data = json.loads(geocache.read_text())
            # Each unique city/state pair must appear in the cache
            assert "Big Cabin,OK" in data
            assert "Tomah,WI" in data
            assert "Gila Bend,AZ" in data
            assert "Amarillo,TX" in data

    @patch("apps.ingestion.management.commands.load_fuel_stations.time.sleep")
    @patch("apps.ingestion.management.commands.load_fuel_stations.Nominatim")
    def test_geocache_prevents_re_geocoding(self, mock_nominatim_cls, mock_sleep):
        """
        Second run should use cached coords and make zero new Nominatim calls.
        """
        mock_geo = _make_mock_geolocator()
        mock_nominatim_cls.return_value = mock_geo

        with tempfile.TemporaryDirectory() as tmpdir:
            geocache = Path(tmpdir) / "geocode_cache.json"
            _run_command(FIXTURE_CSV, geocache)
            first_run_calls = mock_geo.geocode.call_count

            # Second run — geocache is warm, no new calls expected
            mock_geo.geocode.reset_mock()
            _run_command(FIXTURE_CSV, geocache)
            second_run_calls = mock_geo.geocode.call_count

        assert first_run_calls == 4  # 4 unique US city/state pairs
        assert second_run_calls == 0  # all served from cache

    @patch("apps.ingestion.management.commands.load_fuel_stations.time.sleep")
    @patch("apps.ingestion.management.commands.load_fuel_stations.Nominatim")
    def test_failed_geocode_does_not_abort(self, mock_nominatim_cls, mock_sleep):
        """
        If Nominatim returns None for a city, the command must continue and
        write the station with NULL coordinates (not raise an exception).
        """
        # Override: Nominatim returns None for everything
        mock_geo = MagicMock()
        mock_geo.geocode.return_value = None
        mock_nominatim_cls.return_value = mock_geo

        with tempfile.TemporaryDirectory() as tmpdir:
            geocache = Path(tmpdir) / "geocode_cache.json"
            _run_command(FIXTURE_CSV, geocache)  # must not raise

        # All 4 US rows created, coordinates NULL
        assert FuelStation.objects.count() == 4
        assert FuelStation.objects.filter(latitude__isnull=True).count() == 4

    @patch("apps.ingestion.management.commands.load_fuel_stations.time.sleep")
    @patch("apps.ingestion.management.commands.load_fuel_stations.Nominatim")
    def test_price_stored_correctly(self, mock_nominatim_cls, mock_sleep):
        """Retail price must be stored as a Decimal matching the CSV value."""
        mock_nominatim_cls.return_value = _make_mock_geolocator()

        with tempfile.TemporaryDirectory() as tmpdir:
            geocache = Path(tmpdir) / "geocode_cache.json"
            _run_command(FIXTURE_CSV, geocache)

        station = FuelStation.objects.get(opis_id=7)
        assert station.price == Decimal("3.00733333")

    @patch("apps.ingestion.management.commands.load_fuel_stations.time.sleep")
    @patch("apps.ingestion.management.commands.load_fuel_stations.Nominatim")
    def test_dry_run_writes_nothing(self, mock_nominatim_cls, mock_sleep):
        """--dry-run must not write any FuelStation rows."""
        mock_nominatim_cls.return_value = _make_mock_geolocator()

        with tempfile.TemporaryDirectory() as tmpdir:
            geocache = Path(tmpdir) / "geocode_cache.json"
            _run_command(FIXTURE_CSV, geocache, dry_run=True)

        assert FuelStation.objects.count() == 0
