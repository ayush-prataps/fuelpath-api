# fuelpath-api

> A REST API that returns the cost-optimal driving route with refuelling stops
> for any start → finish pair within the contiguous USA.

---

## Table of Contents

1. [Overview](#overview)
2. [Architecture](#architecture)
3. [Setup](#setup)
4. [Running locally](#running-locally)
5. [API Contract](#api-contract)
6. [Management Commands](#management-commands)
7. [Running Tests](#running-tests)
8. [Assumptions & Design Decisions](#assumptions--design-decisions)
9. [Roadmap](#roadmap)

---

## Overview

Given a **start** and **finish** location (free-text US address or `City, ST`),
the API returns:

| Field | Description |
|---|---|
| `route` | Driving route as a GeoJSON FeatureCollection |
| `fuel_stops` | Cost-optimal refuelling sequence (≤ 500-mile vehicle range) |
| `total_gallons` | Total fuel consumed (assumes 10 mpg) |
| `total_fuel_cost_usd` | Total cost at retail prices |

Fuel price data comes from a provided CSV of ~8,150 US truck stops (city/state
only — geocoded offline before serving).

---

## Architecture

```
fuelpath-api/
├── config/                  Django project package (settings, urls, wsgi, asgi)
│   └── settings/
│       ├── base.py          Shared settings (read from env vars)
│       ├── dev.py           Development overrides
│       └── test.py          Test overrides (LocMemCache, fast passwords)
├── apps/
│   ├── fuel/                DRF API layer — serializers, views, urls
│   ├── ingestion/           CSV load + geocoding management commands
│   ├── routing/             OSRM client + geocoding helpers (LocMem cache, opt-in Redis)
│   └── optimizer/           Pure-Python DP solver + KD-tree spatial matcher
├── tests/                   pytest suite
├── data/                    Drop CSV here (gitignored)
└── requirements/
    ├── base.txt
    ├── dev.txt
    └── test.txt
```

**Data flow per request:**

```
POST /api/v1/route/
        │
        ▼
  RouteView.post()
        │  geocode start/finish → (lon, lat)
        ▼
  apps.routing.service.get_route()
        │  ← Django cache look-aside (LocMemCache default, Redis opt-in; 6 h TTL)
        │  → OSRM /route/v1/driving  (on miss)
        ▼
  apps.optimizer.spatial.match_stations_to_route()
        │  KD-tree over route polyline; projects stations, filters 10-mile corridor
        ▼
  apps.optimizer.solver.optimise()
        │  DP over station DAG — globally optimal, not greedy
        ▼
  JSON response
```

---

## Setup

### Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | ≥ 3.12 | |
| PostgreSQL | ≥ 15 | |
| Redis | ≥ 7 | **Optional** — defaults to in-process `LocMemCache` |
| GDAL | ≥ 3.8 | Optional — only if enabling PostGIS |

### 1 — Clone and create a virtual environment

```bash
git clone https://github.com/your-org/fuelpath-api.git
cd fuelpath-api
python3 -m venv .venv
source .venv/bin/activate
```

### 2 — Install dependencies

```bash
pip install -r requirements/dev.txt
```

### 3 — Configure environment variables

```bash
cp .env.example .env
# Edit .env — at minimum set SECRET_KEY, DB_PASSWORD, and DB_NAME.
# Redis is optional: set REDIS_URL only if you want a persistent cache backend.
```

### 4 — Create the database and run migrations

```bash
createdb fuelpath          # or use psql / pgAdmin
python manage.py migrate
```

### 5 — Load fuel-station data

```bash
# Drop your CSV into data/ then run the combined load + geocode command:
python manage.py load_fuel_stations --csv data/fuel-prices.csv
```

This command bulk-loads stations from the CSV, geocodes each city/state via
Nominatim (respecting the 1 req/s rate limit), and persists a geocode cache to
disk so interrupted runs can resume without re-geocoding already-resolved rows.

---

## Running locally

```bash
python manage.py runserver
```

The API is available at `http://localhost:8000/api/v1/`.

---

## API Contract

### `POST /api/v1/route/`

**Request**

```json
{
  "start":  "Chicago, IL",
  "finish": "Houston, TX"
}
```

**Response `200 OK`**

```json
{
  "route": {
    "type": "FeatureCollection",
    "features": [
      {
        "type": "Feature",
        "geometry": {
          "type": "LineString",
          "coordinates": [[...], ...]
        },
        "properties": {
          "distance_m": 1234567.8,
          "duration_s": 43200.0
        }
      }
    ]
  },
  "fuel_stops": [
    {
      "name": "Pilot Travel Center",
      "city": "Memphis",
      "state": "TN",
      "latitude": 35.149,
      "longitude": -90.048,
      "retail_price_usd": 3.45,
      "gallons": 32.5,
      "cost_usd": 112.13
    }
  ],
  "total_gallons": 87.3,
  "total_fuel_cost_usd": 301.09
}
```

**Error envelope** (all 4xx / 5xx responses)

```json
{
  "error": {
    "code": "bad_request",
    "message": "This field is required.",
    "detail": { "start": ["This field is required."] }
  }
}
```

---

## Management Commands

| Command | Purpose |
|---|---|
| `load_fuel_stations --csv <path>` | Bulk-load / upsert stations from CSV **and** geocode in one pass; resumes safely after interruption via on-disk geocode cache |

---

## Running Tests

```bash
# Run the full suite (requires a test DB — pytest-django creates it)
pytest

# Skip coverage report for a faster local run
pytest --no-cov

# Fast: skip DB tests
pytest -m "not django_db"

# With coverage
pytest --cov=apps --cov-report=html
```

Expected baseline: **53 passed, 4 xfailed** (the 4 xfails are end-to-end
`TestRouteEndpointSuccess` tests that require a live OSRM connection).

---

## Assumptions & Design Decisions

| # | Assumption / Decision | Rationale |
|---|---|---|
| 1 | Vehicle starts with a **full tank** | Simplifies the DP initial state; documented in solver to avoid confusion when a short route (distance ≤ range) returns zero stops and $0.00 cost — this is intentional, not a bug |
| 2 | **500-mile range**, **10 mpg** → 50-gallon tank | Configurable via `VEHICLE_RANGE_MILES` / `VEHICLE_MPG` env vars; read through `VehicleConfig` dataclass — no magic numbers in solver code |
| 3 | Fuel-stop selection is a **DP-over-DAG problem**, not greedy | Greedy ("cheapest reachable next stop") is not globally optimal when a slightly-further station is cheap enough to make skipping a closer stop worthwhile. The solver models start + stations + destination as a directed acyclic graph and finds the minimum-cost path in O(N²) |
| 4 | Station-to-route matching uses a **KD-tree** (scipy) over 3D Cartesian coordinates of the OSRM polyline, with a configurable corridor half-width (default **10 miles / 16,093.4 m**) | KD-tree O(log M) vertex lookup avoids O(stations × polyline_points) brute force; 3D Cartesian eliminates latitude-dependent aspect ratio distortion; k=5 nearest-vertex candidates handle hairpin turns and cloverleaf interchanges robustly |
| 5 | **OSRM** responses are cached via Django's cache framework (`LocMemCache` default, opt-in Redis) keyed by rounded coordinate string `osrm:route:{start_lat},{start_lon}:{end_lat},{end_lon}` (~4 decimal places / 11 m precision), TTL 6 h | Prevents duplicate OSRM calls; rounding avoids cache misses on trivial float noise; LocMem requires no external service for local dev / CI |
| 6 | Geocoding the CSV is an **offline, pre-ingestion step** | Nominatim rate-limits to 1 req/s; ~8 k records ≈ 2.5 h offline — unacceptable on the hot path |
| 7 | The API accepts free-text location strings; **geocoding the user's start/finish** happens on the hot path via Nominatim | Adds latency; both geocode results are cached (same Django cache layer, same TTL) so repeated queries for the same city names cost zero additional external calls |
| 8 | No authentication on the API | Assumed internal / demo use; add token auth before any public exposure |
| 9 | Geographic coverage is restricted to the **contiguous United States (CONUS)** via bounding-box geofence (`24.0°N–50.0°N`, `125.0°W–66.0°W`) | Alaska, Hawaii, and offshore territories are excluded since highway road networks and 500-mile truck range apply to continental driving routes |
| 10 | **External call budget**: the assessment constraint ("one call ideal, two-three acceptable") applies specifically to the routing/mapping API | A cache-cold request makes up to 2 Nominatim geocoding calls (start + finish) plus exactly 1 OSRM routing call — 3 external calls total, but only 1 against the routing-API budget. Both layers use look-aside caching so steady-state repeated queries cost 0 external calls |

---

## Roadmap

- [x] Implement `FuelStation` model + migrations (`apps.ingestion`)
- [x] Implement `load_fuel_stations` management command (load + geocode in one pass, crash-safe resume)
- [x] Implement OSRM HTTP client with retry + CONUS geofence (`apps.routing.service`)
- [x] Implement geocoding helpers with caching (`apps.routing.geocoding`)
- [x] Implement spatial route-matching with KD-tree (`apps.optimizer.spatial`)
- [x] Implement DP solver (`apps.optimizer.solver.optimise`)
- [ ] Wire everything together in `RouteView.post()`
- [ ] Add integration tests with OSRM stub responses
- [ ] Consider PostGIS for native geospatial queries
- [ ] Add request-level geocoding cache persistence across restarts (currently in-memory only for user queries)
