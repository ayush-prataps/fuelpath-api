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
│   ├── routing/             OSRM client + Redis caching
│   └── optimizer/           Pure-Python DP solver + spatial matcher
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
        │  ← Redis cache (6 h TTL)
        │  → OSRM /route/v1/driving  (on miss)
        ▼
  apps.optimizer.spatial.match_stations_to_route()
        │  projects stations onto route polyline, filters corridor
        ▼
  apps.optimizer.solver.optimise()
        │  Dijkstra / DP over station DAG
        ▼
  JSON response
```

---

## Setup

### Prerequisites

| Requirement | Version |
|---|---|
| Python | ≥ 3.12 |
| PostgreSQL | ≥ 15 |
| Redis | ≥ 7 |
| GDAL (optional) | ≥ 3.8 — needed only if enabling PostGIS |

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
# Edit .env — at minimum set SECRET_KEY, DB_PASSWORD, and DB_NAME
```

### 4 — Create the database and run migrations

```bash
createdb fuelpath          # or use psql / pgAdmin
python manage.py migrate
```

### 5 — Load fuel-station data

```bash
# Drop your CSV into data/ then:
python manage.py load_stations --csv data/fuel-prices.csv
python manage.py geocode_stations          # runs Nominatim geocoding offline
```

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
| `load_stations --csv <path>` | Bulk-load / upsert fuel stations from CSV |
| `geocode_stations [--limit N] [--delay 1.1]` | Offline geocoding via Nominatim |

---

## Running Tests

```bash
# Run the full suite (requires a test DB — pytest-django creates it)
pytest

# Fast: skip DB tests
pytest -m "not django_db"

# With coverage
pytest --cov=apps --cov-report=html
```

---

## Assumptions & Design Decisions

> _Fill in details as implementation progresses._

| # | Assumption / Decision | Rationale |
|---|---|---|
| 1 | Vehicle starts with a **full tank** | Simplifies the DP initial state |
| 2 | **500-mile range**, **10 mpg** → 50-gallon tank | Configurable via env vars |
| 3 | Fuel-stop selection is a **shortest-path / DP problem** over a station DAG, not greedy "cheapest in range" | Greedy is not globally optimal when cheap stations appear slightly out of order along the route |
| 4 | Station-to-route matching uses a **spatial index** (KD-tree or R-tree) over the OSRM geometry, with a configurable corridor width (default 10 km / ~6 mi) | Avoids scanning all 8 k+ stations per request |
| 5 | **OSRM** responses are cached in Redis keyed by SHA-256(origin\|destination), TTL 6 h | OSRM calls are expensive; most repeated city-pairs reuse the same route |
| 6 | Geocoding the CSV is an **offline, pre-ingestion step** | Nominatim rate-limits to 1 req/s; 8 k records ≈ 2.5 h offline, unacceptable on the hot path |
| 7 | The API accepts free-text location strings; **geocoding the user's start/finish** happens on the hot path (Nominatim or a commercial geocoder) | Adds latency — a future caching layer is noted in the roadmap |
| 8 | No authentication on the API | Assumed internal / demo use; add token auth before any public exposure |

---

## Roadmap

- [ ] Implement `FuelStation` model + migrations (apps.ingestion)
- [ ] Implement `load_stations` management command
- [ ] Implement `geocode_stations` management command
- [ ] Implement OSRM HTTP client (`apps.routing.service._call_osrm`)
- [ ] Implement spatial route-matching (`apps.optimizer.spatial`)
- [ ] Implement DP solver (`apps.optimizer.solver.optimise`)
- [ ] Wire everything together in `RouteView.post()`
- [ ] Add integration tests with OSRM stub responses
- [ ] Consider PostGIS for native geospatial queries
- [ ] Add request-level geocoding cache for start/finish locations
