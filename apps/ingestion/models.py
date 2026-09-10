"""
Ingestion models.

Planned model:

    FuelStation
    -----------
    TRUCKSTOP_NAME  str
    ADDRESS         str
    CITY            str
    STATE           str (2-char code)
    RACK_ID         str (dedup key from the CSV)
    RETAIL_PRICE    Decimal (USD / gallon)
    latitude        Decimal | null  (populated by geocoder command)
    longitude       Decimal | null  (populated by geocoder command)
    geocoded_at     datetime | null

    Spatial index on (latitude, longitude) once PostGIS is wired in.

Models will be implemented when the ingestion work begins.
"""
# No models defined here yet.
