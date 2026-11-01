"""Configurable initial Pondicherry city search area (not a legal boundary)."""

import math
import os

# Reference coordinate published in a Government of Puducherry report:
# https://dste.py.gov.in/PCCC/pdf/Reports/Monitoring-Evaluation-and-Learning-Framework-for-Puducherry.pdf
# The 10 km radius is a provisional product search area, not an administrative boundary.
DEFAULT_CENTER_LAT = 11.916064
DEFAULT_CENTER_LNG = 79.812325
DEFAULT_RADIUS_METERS = 10_000


def _setting(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc


def supported_area() -> dict:
    radius = _setting("PONDICHERRY_SEARCH_RADIUS_METERS", DEFAULT_RADIUS_METERS)
    if not 1 <= radius <= 20_000:
        raise ValueError("Pondicherry search area settings are outside valid ranges")
    return {"center": {"lat": DEFAULT_CENTER_LAT, "lng": DEFAULT_CENTER_LNG}, "radius_meters": radius,
            "name": "Pondicherry city initial search area", "boundary_type": "provisional radius; not a municipal boundary"}


def location_in_supported_area(location: dict | None) -> bool:
    """Reject absent, invalid, and out-of-radius coordinates."""
    if not isinstance(location, dict):
        return False
    try:
        lat = float(location.get("lat"))
        lng = float(location.get("lng"))
        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            return False
        area = supported_area()
    except (TypeError, ValueError):
        return False
    center = area["center"]
    earth_radius = 6_371_000
    lat1, lat2 = math.radians(center["lat"]), math.radians(lat)
    delta_lat = lat2 - lat1
    delta_lng = math.radians(lng - center["lng"])
    haversine = math.sin(delta_lat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lng / 2) ** 2
    distance = earth_radius * 2 * math.atan2(math.sqrt(haversine), math.sqrt(1 - haversine))
    return distance <= area["radius_meters"]
