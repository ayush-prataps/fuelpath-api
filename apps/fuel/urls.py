"""URL patterns for the fuel API (mounted at /api/v1/ by config.urls)."""

from django.urls import path

from .views import RouteView

app_name = "fuel"

urlpatterns = [
    path("route/", RouteView.as_view(), name="route"),
]
