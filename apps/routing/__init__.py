"""
apps/routing — OSRM client & route caching
==========================================
Wraps the OSRM HTTP API, handles serialisation of the response into a
GeoJSON LineString, and caches the result in Redis keyed by
(origin_lon, origin_lat, dest_lon, dest_lat).
"""
