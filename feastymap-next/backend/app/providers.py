"""Keyless development adapters for OpenStreetMap data and public routing services.

Public Photon, Overpass, OSRM, and OSM tile endpoints are fair-use services, not
production SLAs. Keep requests small, cache responses, and make endpoint URLs configurable.
"""

import asyncio
import copy
import math
import os
import re
import time
import weakref
from collections import OrderedDict
from datetime import datetime, timezone
from urllib.parse import urlsplit

import httpx

from .geography import location_in_supported_area, supported_area

PHOTON_URL = "https://photon.komoot.io/api"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
WEATHER_URL = "https://api.open-meteo.com/v1/forecast"
OSRM_URLS = {
    "DRIVE": ("OSRM_DRIVING_URL", "https://router.project-osrm.org/route/v1/driving"),
    "WALK": ("OSRM_WALKING_URL", "https://routing.openstreetmap.de/routed-foot/route/v1/driving"),
    "BICYCLE": ("OSRM_BICYCLING_URL", "https://routing.openstreetmap.de/routed-bike/route/v1/driving"),
}
PROVIDER_USER_AGENT = "FeastyMapNext/1.0 (Pondicherry outing planner)"
CACHE_TTL_SECONDS = 15 * 60
MAX_CACHE_ENTRIES = 128

class ProviderError(RuntimeError):
    pass


# Cache only small, bounded result sets. A process restart clears this cache.
_cache: OrderedDict[str, tuple[float, object]] = OrderedDict()
_loop_locks: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()
_last_request: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def _cache_get(key: str):
    item = _cache.get(key)
    if item is None:
        return None
    expires_at, value = item
    if expires_at <= time.monotonic():
        _cache.pop(key, None)
        return None
    _cache.move_to_end(key)
    return copy.deepcopy(value)


def _cache_set(key: str, value) -> None:
    _cache[key] = (time.monotonic() + CACHE_TTL_SECONDS, copy.deepcopy(value))
    _cache.move_to_end(key)
    while len(_cache) > MAX_CACHE_ENTRIES:
        _cache.popitem(last=False)


async def _pace_requests(host: str) -> None:
    """Keep requests to each shared public host at or below one per second."""
    loop = asyncio.get_running_loop()
    locks = _loop_locks.setdefault(loop, {})
    last = _last_request.setdefault(loop, {})
    lock = locks.setdefault(host, asyncio.Lock())
    async with lock:
        wait = 1.0 - (time.monotonic() - last.get(host, 0.0))
        if wait > 0:
            await asyncio.sleep(wait)
        last[host] = time.monotonic()


async def _request_json(service: str, url: str, *, method: str = "GET", params=None, data=None) -> dict:
    host = urlsplit(url).hostname or url
    await _pace_requests(host)
    headers = {
        "Accept": "application/json",
        "User-Agent": os.getenv("PUBLIC_PROVIDER_USER_AGENT", PROVIDER_USER_AGENT),
    }
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=12, headers=headers) as client:
                response = await client.request(method, url, params=params, data=data)
            if response.status_code == 429:
                raise ProviderError(f"{service} is rate-limiting requests. Wait briefly and try again.")
            if response.status_code >= 500 and attempt == 0:
                await asyncio.sleep(0.5)
                continue
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ProviderError(f"{service} returned an unexpected response.")
            return payload
        except ProviderError:
            raise
        except httpx.TimeoutException as exc:
            raise ProviderError(f"{service} did not respond in time. Try again shortly.") from exc
        except httpx.HTTPStatusError as exc:
            raise ProviderError(f"{service} returned HTTP {exc.response.status_code}. Try again later.") from exc
        except (httpx.RequestError, ValueError) as exc:
            if attempt == 0 and isinstance(exc, httpx.RequestError):
                await asyncio.sleep(0.5)
                continue
            raise ProviderError(f"{service} is temporarily unavailable. Try again shortly.") from exc
    raise ProviderError(f"{service} is temporarily unavailable. Try again shortly.")


def _valid_location(location: dict | None) -> bool:
    if not isinstance(location, dict):
        return False
    try:
        lat, lng = float(location.get("lat")), float(location.get("lng"))
    except (TypeError, ValueError):
        return False
    return math.isfinite(lat) and math.isfinite(lng) and -90 <= lat <= 90 and -180 <= lng <= 180


async def resolve_origin(query: str) -> dict:
    """Resolve a start point with Photon. Starts may be outside the destination radius."""
    text = query.strip()
    coordinate = re.fullmatch(r"\s*(-?\d{1,2}(?:\.\d+)?)\s*,\s*(-?\d{1,3}(?:\.\d+)?)\s*", text)
    if coordinate:
        location = {"lat": float(coordinate.group(1)), "lng": float(coordinate.group(2))}
        if _valid_location(location):
            return location
        raise ProviderError("Starting coordinates are outside valid latitude/longitude ranges.")
    if len(text) < 2:
        raise ProviderError("Enter a more specific starting location.")
    key = f"photon:{text.casefold()}"
    cached = _cache_get(key)
    if cached is not None:
        return cached
    center = supported_area()["center"]
    url = os.getenv("PHOTON_API_URL", PHOTON_URL).rstrip("/")
    payload = await _request_json("Photon geocoding", url, params={
        "q": text,
        "limit": 5,
        "lang": "en",
        "countrycode": "IN",
        "lat": center["lat"],
        "lon": center["lng"],
        "zoom": 8,
    })
    features = payload.get("features", [])
    for feature in features:
        props = feature.get("properties", {})
        coordinates = feature.get("geometry", {}).get("coordinates", [])
        if props.get("countrycode", "IN").upper() != "IN" or len(coordinates) < 2:
            continue
        location = {"lat": coordinates[1], "lng": coordinates[0]}
        if _valid_location(location):
            _cache_set(key, location)
            return location
    raise ProviderError("Photon could not resolve that starting location. Try a specific place or coordinates such as 11.91, 79.81.")


_CATEGORY_FILTERS = {
    "beach": ('["natural"="beach"]', '["leisure"="beach_resort"]'),
    "cafe": ('["amenity"="cafe"]',),
    "restaurant": ('["amenity"="restaurant"]',),
    "heritage": ('["historic"]', '["tourism"="museum"]', '["tourism"="gallery"]'),
    "temple": ('["amenity"="place_of_worship"]["religion"="hindu"]', '["building"="temple"]'),
    "shopping": ('["shop"]', '["amenity"="marketplace"]'),
    "nature": ('["leisure"="park"]', '["leisure"="nature_reserve"]', '["natural"="wood"]', '["tourism"="viewpoint"]'),
    "activity": ('["sport"]', '["leisure"="sports_centre"]', '["leisure"="swimming_pool"]', '["tourism"="attraction"]'),
}


def _osm_boolean(value: str | None) -> bool | None:
    if not value:
        return None
    normalized = value.casefold().strip()
    if normalized in {"yes", "only", "true", "1"}:
        return True
    if normalized in {"no", "false", "0"}:
        return False
    return None


def _indoor_types(tags: dict) -> list[str]:
    types = []
    if tags.get("tourism") == "museum":
        types.append("museum")
    if tags.get("tourism") == "gallery":
        types.append("art_gallery")
    if tags.get("amenity") == "cinema":
        types.append("movie_theater")
    if tags.get("amenity") == "library":
        types.append("library")
    if tags.get("shop") == "mall":
        types.append("shopping_mall")
    if tags.get("amenity") == "theatre":
        types.append("performing_arts_theater")
    return types


def _normalize_osm_element(element: dict, category: str | None = None) -> dict | None:
    tags = element.get("tags") or {}
    name = tags.get("name") or tags.get("name:en")
    if not name:
        return None
    point = element.get("center") or element
    location = {"lat": point.get("lat"), "lng": point.get("lon")}
    if not _valid_location(location):
        return None
    osm_type = str(element.get("type", "")).casefold()
    osm_id = str(element.get("id", ""))
    if osm_type not in {"node", "way", "relation"} or not osm_id.isdigit():
        return None
    address_parts = [
        " ".join(filter(None, [tags.get("addr:housenumber"), tags.get("addr:street")])),
        tags.get("addr:suburb") or tags.get("addr:neighbourhood"),
        tags.get("addr:city"),
        tags.get("addr:postcode"),
    ]
    address = ", ".join(part for part in address_parts if part) or None
    primary_type = next((tags.get(key) for key in ("amenity", "tourism", "historic", "leisure", "shop", "natural", "sport") if tags.get(key)), "place")
    vegetarian = _osm_boolean(tags.get("diet:vegetarian"))
    wheelchair = _osm_boolean(tags.get("wheelchair"))
    dietary_options = {
        preference: _osm_boolean(tags.get("diet:" + osm_tag))
        for preference, osm_tag in (("vegetarian", "vegetarian"), ("vegan", "vegan"), ("halal", "halal"), ("jain", "jain"), ("gluten_free", "gluten_free"))
        if _osm_boolean(tags.get("diet:" + osm_tag)) is not None
    }
    uri = f"https://www.openstreetmap.org/{osm_type}/{osm_id}"
    return {
        "id": f"osm:{osm_type}:{osm_id}",
        "provider": "openstreetmap_overpass",
        "name": name,
        "address": address,
        "category": category or primary_type,
        "location": {"lat": float(location["lat"]), "lng": float(location["lng"])},
        "rating": None,
        "open_now": None,
        "opening_hours": [tags["opening_hours"]] if tags.get("opening_hours") else [],
        "opening_periods": [],
        "price_level": None,
        "price_range": None,
        "cost_estimate": None,
        "osm_uri": uri,
        "source": "OpenStreetMap contributors via Overpass API",
        "primary_type": primary_type,
        "types": _indoor_types(tags),
        "accessibility_options": {"wheelchairAccessibleEntrance": wheelchair} if wheelchair is not None else {},
        "serves_vegetarian_food": vegetarian,
        "dietary_options": dietary_options,
        "good_for_children": None,
        "good_for_groups": None,
        "serves_breakfast": None,
        "serves_lunch": None,
        "serves_dinner": None,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "freshness": "OpenStreetMap data; update time varies by mapped object and public instance",
        "verification": "OpenStreetMap contributor-mapped; venue status and details may be incomplete",
        "osm_tags": tags,
    }


def _overpass_search_query(category: str, center: dict, radius: int) -> str:
    filters = _CATEGORY_FILTERS.get(category)
    if not filters:
        return ""
    selectors = "\n".join(f"  nwr{osm_filter}(around:{radius},{center['lat']},{center['lng']});" for osm_filter in filters)
    return f"[out:json][timeout:18][maxsize:67108864];\n(\n{selectors}\n);\nout center tags 100 qt;"


def _matches_local_query(place: dict, query: str) -> bool:
    normalized = query.casefold().strip()
    if normalized in {"", "pondicherry", "puducherry", "pondy"}:
        return True
    tags = place.get("osm_tags", {})
    haystack = " ".join([place.get("name", ""), place.get("address", "") or "", tags.get("addr:suburb", ""), tags.get("addr:neighbourhood", ""), tags.get("addr:quarter", ""), tags.get("addr:city", "")]).casefold()
    return normalized in haystack


async def discover_places(query: str, category: str, limit: int = 12, **_unused) -> list[dict]:
    """Find nearby OSM-mapped places, then enforce the unchanged Pondicherry radius."""
    if category == "events":
        # OpenStreetMap's POI tags are not a live event listing feed.
        return []
    if category not in _CATEGORY_FILTERS:
        raise ProviderError("This place category is not supported by the OpenStreetMap search adapter.")
    try:
        area = supported_area()
    except ValueError as exc:
        raise ProviderError("Pondicherry search area configuration is invalid.") from exc
    center = area["center"]
    radius = int(area["radius_meters"])
    cache_key = f"overpass:{category}:{radius}:{center['lat']:.6f}:{center['lng']:.6f}"
    elements = _cache_get(cache_key)
    if elements is None:
        query_text = _overpass_search_query(category, center, radius)
        payload = await _request_json("Overpass nearby-place search", os.getenv("OVERPASS_API_URL", OVERPASS_URL), method="POST", data={"data": query_text})
        if payload.get("remark"):
            raise ProviderError("Overpass could not complete this search. Try again shortly.")
        elements = payload.get("elements", [])
        _cache_set(cache_key, elements)
    results = []
    seen = set()
    for element in elements:
        place = _normalize_osm_element(element, category)
        if not place or place["id"] in seen or not _matches_local_query(place, query):
            continue
        if not location_in_supported_area(place["location"]):
            continue
        seen.add(place["id"])
        results.append(place)
    results.sort(key=lambda place: (
        (place["location"]["lat"] - center["lat"]) ** 2 + (place["location"]["lng"] - center["lng"]) ** 2,
        place["name"].casefold(),
    ))
    return results[:max(1, min(int(limit), 20))]


_OSM_ID = re.compile(r"^osm:(node|way|relation):(\d+)$")


async def place_details_many(place_ids: list[str]) -> dict[str, dict]:
    """Refresh several saved OSM object IDs in one bounded Overpass request."""
    parsed = []
    for place_id in dict.fromkeys(place_ids):
        match = _OSM_ID.fullmatch(place_id)
        if match:
            parsed.append((place_id, match.group(1), match.group(2)))
    if not parsed:
        return {}
    cache_key = "overpass-details:" + ",".join(sorted(item[0] for item in parsed))
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    clauses = []
    for osm_type in ("node", "way", "relation"):
        ids = sorted({osm_id for _, kind, osm_id in parsed if kind == osm_type})
        if ids:
            clauses.append(f"  {osm_type}(id:{','.join(ids)});")
    query_text = "[out:json][timeout:12][maxsize:16777216];\n(\n" + "\n".join(clauses) + "\n);\nout center tags;"
    payload = await _request_json("Overpass saved-place refresh", os.getenv("OVERPASS_API_URL", OVERPASS_URL), method="POST", data={"data": query_text})
    if payload.get("remark"):
        raise ProviderError("OpenStreetMap could not refresh the saved places. The itinerary is still saved; retry later.")
    normalized = {}
    by_key = {(kind, osm_id): place_id for place_id, kind, osm_id in parsed}
    for element in payload.get("elements", []):
        place_id = by_key.get((element.get("type"), str(element.get("id"))))
        if not place_id:
            continue
        place = _normalize_osm_element(element)
        if place:
            normalized[place_id] = place
    _cache_set(cache_key, normalized)
    return normalized


async def place_details(place_id: str) -> dict:
    details = await place_details_many([place_id])
    if place_id not in details:
        raise ProviderError("This OpenStreetMap place could not be refreshed. It may have been removed or its coordinates are unknown.")
    return details[place_id]


async def route(origin: dict, destination: dict, mode: str = "DRIVE") -> dict:
    """Get a real OSRM road route for driving, walking, or bicycling."""
    if not _valid_location(origin) or not _valid_location(destination):
        raise ProviderError("A valid start and destination coordinate are required for routing.")
    setting = OSRM_URLS.get(mode)
    if not setting:
        raise ProviderError("Public OSRM routing does not provide transit routes. Choose drive, walk, or bicycle.")
    variable, default_url = setting
    endpoint = os.getenv(variable, default_url).rstrip("/")
    url = f"{endpoint}/{origin['lng']},{origin['lat']};{destination['lng']},{destination['lat']}"
    key = f"osrm:{mode}:{origin['lat']:.6f},{origin['lng']:.6f}:{destination['lat']:.6f},{destination['lng']:.6f}"
    cached = _cache_get(key)
    if cached is not None:
        return cached
    payload = await _request_json("OSRM route service", url, params={"overview": "full", "geometries": "geojson", "steps": "false"})
    routes = payload.get("routes", [])
    if payload.get("code") != "Ok" or not routes:
        raise ProviderError("OSRM could not find a road route for this leg. Try a nearby start point or another travel mode.")
    route_result = routes[0]
    try:
        duration_seconds = math.ceil(float(route_result["duration"]))
        distance_meters = round(float(route_result["distance"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise ProviderError("OSRM returned incomplete route data.") from exc
    if duration_seconds < 0 or distance_meters < 0:
        raise ProviderError("OSRM returned invalid route measurements.")
    result = {
        "distance_meters": distance_meters,
        "duration": f"{duration_seconds}s",
        "geometry": route_result.get("geometry"),
        "source": "OSRM on OpenStreetMap data",
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
    }
    _cache_set(key, result)
    return result


async def request_with_retries(client: httpx.AsyncClient, method: str, url: str, **kwargs):
    """Retry one transient non-rate-limit failure for the Open-Meteo adapter."""
    for attempt in range(2):
        try:
            response = await client.request(method, url, **kwargs)
            if response.status_code == 429:
                response.raise_for_status()
            if response.status_code < 500 or attempt == 1:
                response.raise_for_status()
                return response
        except httpx.RequestError:
            if attempt == 1:
                raise
        await asyncio.sleep(0.3)


async def weather(lat: float, lng: float, date: str) -> dict:
    params = {"latitude": lat, "longitude": lng, "current": "temperature_2m,precipitation,weather_code", "daily": "weather_code,precipitation_probability_max,temperature_2m_max,temperature_2m_min", "timezone": "Asia/Kolkata", "forecast_days": 16}
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            response = await request_with_retries(client, "GET", os.getenv("WEATHER_API_URL", WEATHER_URL), params=params)
        except httpx.HTTPError as exc:
            raise ProviderError("Weather forecast is temporarily unavailable.") from exc
    data = response.json()
    days = data.get("daily", {}).get("time", [])
    if date in days:
        idx = days.index(date)
        daily = data.get("daily", {})
        probabilities = daily.get("precipitation_probability_max", [])
        codes = daily.get("weather_code", [])
        return {"source": "Open-Meteo", "retrieved_at": datetime.now(timezone.utc).isoformat(), "current": data.get("current"), "date": date,
                "precipitation_probability_max": probabilities[idx] if idx < len(probabilities) else None, "weather_code": codes[idx] if idx < len(codes) else None, "freshness": "forecast"}
    return {"source": "Open-Meteo", "retrieved_at": datetime.now(timezone.utc).isoformat(), "current": data.get("current"), "date": date, "daily_forecast": None, "freshness": "date outside available forecast"}
