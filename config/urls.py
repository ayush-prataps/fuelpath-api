"""URL configuration for fuelpath-api."""

from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    # Fuel-path REST API — v1
    path("api/v1/", include("apps.fuel.urls", namespace="fuel")),
]
