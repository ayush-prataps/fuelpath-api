"""
Routing models.

Planned model:

    RouteCache
    ----------
    cache_key   str  (SHA-256 of origin+destination coords)
    geojson     JSONField  (FeatureCollection with a single LineString)
    distance_m  float      (total route distance in metres, from OSRM)
    duration_s  float      (estimated drive time in seconds, from OSRM)
    created_at  datetime
    expires_at  datetime   (used to enforce TTL outside Redis)

Models will be implemented when the routing work begins.
"""
# No models defined here yet.
