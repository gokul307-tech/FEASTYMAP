import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from dotenv import load_dotenv

from .geography import location_in_supported_area, supported_area
from .planner import (assess_place_constraints, build_plan, estimate_visit_minutes,
                      indoor_alternatives, is_open_for_visit, weather_risk)
from .providers import ProviderError, discover_places, place_details, place_details_many, resolve_origin, route, weather
from .schemas import PlanRequest, ReplaceStopRequest, SavedPlanRequest

# Load only this isolated application's .env; never read/modify the legacy workspace .env.
load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)

app = FastAPI(title="FeastyMap API", version="1.1.0", description="Pondicherry outing discovery and planning API")
allowed_origins = [origin.strip() for origin in os.getenv("CORS_ALLOWED_ORIGINS", "http://localhost:4028").split(",") if origin.strip()]
app.add_middleware(CORSMiddleware, allow_origins=allowed_origins, allow_methods=["GET", "POST", "DELETE"], allow_headers=["Content-Type"])

# Lightweight per-process limit. Production multi-instance deployments should use a shared gateway/store.
_requests: dict[str, list[float]] = {}
_RATE_LIMIT = 40
_RATE_WINDOW_SECONDS = 60
_engine = None

@app.middleware("http")
async def request_limits(request: Request, call_next):
    size = request.headers.get("content-length")
    if size and size.isdigit() and int(size) > 65536:
        return JSONResponse(status_code=413, content={"detail": "Request body exceeds 64 KB"})
    host = request.client.host if request.client else "unknown"
    now = time.monotonic()
    recent = [stamp for stamp in _requests.get(host, []) if now - stamp < _RATE_WINDOW_SECONDS]
    if len(recent) >= _RATE_LIMIT:
        return JSONResponse(status_code=429, content={"detail": "Rate limit reached. Please wait and retry."}, headers={"Retry-After": "60"})
    recent.append(now)
    _requests[host] = recent
    return await call_next(request)

def database_engine():
    global _engine
    url = os.getenv("DATABASE_URL")
    if not url:
        raise HTTPException(status_code=503, detail="Saved plans are unavailable until DATABASE_URL is configured and the migration is applied.")
    if _engine is None:
        try:
            _engine = create_engine(url, pool_pre_ping=True, connect_args={"connect_timeout": 3})
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Database configuration is invalid.") from exc
    return _engine

def database_unavailable(exc: Exception):
    raise HTTPException(status_code=503, detail="Database is unavailable. Check DATABASE_URL and apply the saved itinerary migration.") from exc

@app.get("/health")
async def health():
    return {"status": "ok", "time": datetime.now(timezone.utc).isoformat(), "database_configured": bool(os.getenv("DATABASE_URL"))}

@app.get("/api/places")
async def places(category: str = Query(min_length=2, max_length=40), query: str = Query(default="Pondicherry", min_length=2, max_length=100)):
    search_term = query.casefold()
    unsupported_regions = ("karaikal", "mahe", "yanam")
    if any(region in search_term for region in unsupported_regions):
        raise HTTPException(422, "Place search is limited to Pondicherry city. Karaikal, Mahe and Yanam are not supported destinations.")
    try:
        results = await discover_places(query, category, 12)
        results = [place for place in results if location_in_supported_area(place.get("location"))]
        notice = ("OpenStreetMap does not provide a live event listing; no event candidates were searched." if category == "events" else
                  "Mapped place details may be incomplete or out of date; public services are best effort.")
        return {"places": results, "source": "OpenStreetMap contributors via Overpass API", "attribution": "© OpenStreetMap contributors", "notice": notice, "scope": supported_area(), "retrieved_at": datetime.now(timezone.utc).isoformat()}
    except ProviderError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=503, detail="Pondicherry search area configuration is invalid.") from exc

@app.get("/api/places/{place_id}")
async def get_place(place_id: str):
    if len(place_id) > 200 or "/" in place_id:
        raise HTTPException(422, "Invalid place ID")
    try:
        place = await place_details(place_id)
        if not location_in_supported_area(place.get("location")):
            raise HTTPException(404, "This place is outside the supported Pondicherry area or lacks verified coordinates.")
        return place
    except ProviderError as exc:
        raise HTTPException(503, detail=str(exc)) from exc

@app.get("/api/weather")
async def forecast(date: str = Query(pattern=r"^\d{4}-\d{2}-\d{2}$")):
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError as exc:
        raise HTTPException(422, "Invalid calendar date") from exc
    try:
        center = supported_area()["center"]
        return await weather(center["lat"], center["lng"], date)
    except ValueError as exc:
        raise HTTPException(503, "Pondicherry search area configuration is invalid.") from exc
    except ProviderError as exc:
        raise HTTPException(503, detail=str(exc)) from exc

async def get_itinerary(request: PlanRequest, exclude_ids: set[str] | None = None):
    exclude_ids = exclude_ids or set()
    try:
        supported_area()
    except ValueError as exc:
        raise ProviderError("Pondicherry search area configuration is invalid.") from exc
    mode = {"drive": "DRIVE", "walk": "WALK", "bicycle": "BICYCLE", "transit": "TRANSIT"}[request.transport]
    if mode == "TRANSIT":
        raise HTTPException(422, "Transit routing is not available from the configured open routing provider. Choose drive, walk, or bicycle.")
    origin = await resolve_origin(request.start_location)
    starting_point = origin.copy()
    start_at = datetime.strptime(request.date + " " + request.start_time, "%Y-%m-%d %H:%M")
    elapsed = 0
    candidates = []
    legs = []
    discovered_pool = []
    rejected_by_constraint = 0
    scoped_candidate_count = 0
    unsupported_locations = 0
    for category in request.interests:
        alternatives = await discover_places(
            "Pondicherry", category, 5,
            include_dietary=bool(request.dietary_preferences),
            include_group=request.group_type == "family" or request.group_size >= 6,
            include_meal=category in ("cafe", "restaurant"),
        )
        scoped_places = [place for place in alternatives if location_in_supported_area(place.get("location"))]
        unsupported_locations += len(alternatives) - len(scoped_places)
        scoped_candidate_count += len(scoped_places)
        discovered_pool.extend(scoped_places)
        evaluated = [(place, assess_place_constraints(place, request.model_dump())) for place in scoped_places]
        rejected_by_constraint += sum(not assessment["eligible"] for _, assessment in evaluated)
        evaluated = [(place, assessment) for place, assessment in evaluated if assessment["eligible"]]
        evaluated.sort(key=lambda pair: (pair[1]["unknown_count"], pair[0].get("rating") is None,
                                         -(pair[0].get("rating") or 0)))
        for place, _assessment in evaluated:
            if place["id"] in exclude_ids or any(existing["id"] == place["id"] for existing in candidates):
                continue
            leg = await route(origin, place["location"], mode)
            travel = max(0, (int(leg["duration"].rstrip("s")) + 59) // 60)
            visit = estimate_visit_minutes(category)
            if elapsed + travel + visit > request.duration_hours * 60:
                continue
            if is_open_for_visit(place, start_at + timedelta(minutes=elapsed + travel), visit) is False:
                continue
            candidates.append(place)
            legs.append(leg)
            elapsed += travel + visit
            origin = place["location"]
            break
    if not candidates:
        explanation = ("All discovered candidates were outside the configured Pondicherry search area or lacked verified coordinates." if unsupported_locations and not rejected_by_constraint else
                       "No provider candidates with verified coordinates were returned inside the configured Pondicherry search area." if not scoped_candidate_count else
                       "Provider data conflicts with one or more dietary, accessibility or group requirements." if rejected_by_constraint else
                       "No discovered place had a usable route and schedule within the requested outing duration.")
        raise HTTPException(422, f"No itinerary could be made. {explanation} Try relaxing a constraint, changing the interests or visit time, or increasing the duration.")
    result = build_plan(request.model_dump(), candidates, legs)
    try:
        weather_center = supported_area()["center"]
        result["weather"] = await weather(weather_center["lat"], weather_center["lng"], request.date)
        risky, advisory = weather_risk(result["weather"])
        result["weather_advisory"] = advisory
        if risky:
            result["weather_alternatives"] = indoor_alternatives(discovered_pool, {s["place"]["id"] for s in result["stops"]})
            if not result["weather_alternatives"]:
                result["warnings"].append("No verified indoor alternatives were present in the places already discovered; no stop was substituted.")
    except ProviderError:
        result["weather"] = None
        result["weather_advisory"] = "Weather data is temporarily unavailable; outdoor conditions are unknown."
    if result["stops"] and not any(stop["planned_open"] is True for stop in result["stops"]):
        result["warnings"].append("Opening hours could not be verified for any selected stop.")
    return {"itinerary": result, "places_source": "OpenStreetMap contributors via Overpass API", "route_source": "OSRM on OpenStreetMap data", "scope": supported_area()}

@app.post("/api/itineraries")
async def itinerary(request: PlanRequest):
    try:
        return await get_itinerary(request)
    except ProviderError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

@app.post("/api/itineraries/replace-stop")
async def replace_stop(body: ReplaceStopRequest):
    stops = body.itinerary.get("stops", [])
    if body.stop_index >= len(stops):
        raise HTTPException(422, "Stop index does not exist in this itinerary.")
    old = stops[body.stop_index]
    excluded = set(body.excluded_place_ids)
    excluded.update(stop.get("place", {}).get("id") for stop in stops if stop.get("place", {}).get("id"))
    try:
        alternatives = await discover_places(
            "Pondicherry", body.category, 5,
            include_dietary=bool(body.request.dietary_preferences),
            include_group=body.request.group_type == "family" or body.request.group_size >= 6,
            include_meal=body.category in ("cafe", "restaurant"),
        )
        alternatives = [place for place in alternatives if location_in_supported_area(place.get("location")) and place["id"] not in excluded and
                        assess_place_constraints(place, body.request.model_dump())["eligible"]][:3]
        if not alternatives:
            raise HTTPException(404, "No in-scope Pondicherry alternative with verified coordinates was returned. Your current plan is unchanged.")
        for stop in stops:
            if not location_in_supported_area(stop.get("place", {}).get("location")):
                raise HTTPException(422, "This itinerary contains a stop outside the configured Pondicherry area or without verified coordinates; replacement was not applied.")
        start = datetime.strptime(body.request.date + " " + body.request.start_time, "%Y-%m-%d %H:%M")
        mode = {"drive": "DRIVE", "walk": "WALK", "bicycle": "BICYCLE", "transit": "TRANSIT"}[body.request.transport]
        if mode == "TRANSIT":
            raise HTTPException(422, "Transit routing is not available from the configured open routing provider. Choose drive, walk, or bicycle.")
        start_origin = await resolve_origin(body.request.start_location) if body.stop_index == 0 else None
        any_route_succeeded = False
        for alternative in alternatives:
            updated = [dict(stop) for stop in stops]
            updated[body.stop_index] = {"place": alternative, "duration_minutes": estimate_visit_minutes(body.category),
                "reason": f"Alternative selected in the {body.category} category; opening and suitability details are provider-reported."}
            previous = start_origin if body.stop_index == 0 else updated[body.stop_index - 1].get("place", {}).get("location")
            if not previous:
                raise HTTPException(422, "The itinerary is missing a previous stop location.")
            try:
                incoming = await route(previous, alternative["location"], mode)
                outgoing = None
                if body.stop_index + 1 < len(updated):
                    next_location = updated[body.stop_index + 1].get("place", {}).get("location")
                    if not next_location:
                        raise HTTPException(422, "The itinerary is missing a following stop location.")
                    outgoing = await route(alternative["location"], next_location, mode)
            except ProviderError:
                continue
            any_route_succeeded = True
            updated[body.stop_index]["travel_minutes"] = max(0, (int(incoming["duration"].rstrip("s")) + 59) // 60)
            updated[body.stop_index]["travel_distance_meters"] = incoming["distance_meters"]
            updated[body.stop_index]["route_geometry"] = incoming.get("geometry")
            if outgoing is not None:
                updated[body.stop_index + 1]["travel_minutes"] = max(0, (int(outgoing["duration"].rstrip("s")) + 59) // 60)
                updated[body.stop_index + 1]["travel_distance_meters"] = outgoing["distance_meters"]
                updated[body.stop_index + 1]["route_geometry"] = outgoing.get("geometry")

            elapsed = distance = travel_total = 0
            suitable = True
            for stop in updated:
                place = stop.get("place", {})
                if not place.get("location"):
                    raise HTTPException(422, "The itinerary contains a stop without verified location data.")
                if not location_in_supported_area(place["location"]):
                    raise HTTPException(422, "The itinerary contains a stop outside the configured Pondicherry area.")
                duration = stop.get("duration_minutes", estimate_visit_minutes(place.get("category", "activity")))
                travel = stop.get("travel_minutes")
                leg_distance = stop.get("travel_distance_meters")
                if not isinstance(duration, int) or duration < 15 or duration > 360 or not isinstance(travel, int) or travel < 0 or not isinstance(leg_distance, int) or leg_distance < 0:
                    raise HTTPException(422, "The itinerary contains invalid duration or route data.")
                state = is_open_for_visit(place, start + timedelta(minutes=elapsed + travel), duration)
                if state is False:
                    suitable = False
                    break
                elapsed += travel
                stop["duration_minutes"] = duration
                stop["planned_open"] = state
                stop["start_minute_offset"] = elapsed
                stop["start_time"] = (start + timedelta(minutes=elapsed)).isoformat()
                stop["estimated_cost_inr"] = None
                elapsed += duration
                travel_total += travel
                distance += leg_distance
            if not suitable or elapsed > body.request.duration_hours * 60:
                continue
            body.itinerary.update({"stops": updated, "total_duration_minutes": elapsed, "travel_minutes": travel_total,
                "distance_meters": distance, "estimated_total_inr": None})
            return {"itinerary": body.itinerary, "replacement": {"old_place_id": old.get("place", {}).get("id"), "new_place_id": alternative["id"]}}
        if not any_route_succeeded:
            raise ProviderError("Routes could not be recalculated for replacement options.")
        raise HTTPException(422, "No alternative fits the outing duration and provider opening hours. Your current plan is unchanged.")
    except ProviderError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

@app.post("/api/itineraries/saved")
async def save_itinerary(body: SavedPlanRequest):
    for stop in body.itinerary.get("stops", []):
        if not location_in_supported_area(stop.get("place", {}).get("location")):
            raise HTTPException(422, "Saved itinerary contains a stop outside the configured Pondicherry area or without verified coordinates.")
    engine = database_engine()
    summary = {key: body.itinerary.get(key) for key in ("total_duration_minutes", "travel_minutes", "distance_meters", "budget_inr", "estimated_total_inr")}
    try:
        with engine.begin() as conn:
            row = conn.execute(text("""INSERT INTO itineraries(user_id,city,plan_date,request,summary)
                VALUES(:user_id,'Pondicherry',:plan_date,CAST(:request AS jsonb),CAST(:summary AS jsonb)) RETURNING id::text,created_at"""),
                {"user_id": str(body.owner_id), "plan_date": body.request.date, "request": json.dumps(body.request.model_dump()), "summary": json.dumps(summary)}).mappings().one()
            for position, stop in enumerate(body.itinerary.get("stops", []), 1):
                place_id = stop.get("place", {}).get("id")
                if not place_id: continue
                conn.execute(text("""INSERT INTO itinerary_stops(itinerary_id,position,provider,provider_place_id,category,scheduled_start,visit_duration_minutes)
                    VALUES(CAST(:itinerary_id AS uuid),:position,:provider,:place_id,:category,:scheduled_start,:duration)"""),
                    {"itinerary_id": row["id"], "position": position, "provider": stop.get("place", {}).get("provider", "openstreetmap_overpass"), "place_id": place_id, "category": stop.get("place", {}).get("category", "activity"), "scheduled_start": stop.get("start_time"), "duration": stop.get("duration_minutes")})
        return {"id": row["id"], "created_at": row["created_at"].isoformat(), "summary": summary}
    except SQLAlchemyError as exc:
        database_unavailable(exc)

@app.get("/api/itineraries/saved")
async def list_saved(owner_id: UUID = Query()):
    engine = database_engine()
    try:
        with engine.connect() as conn:
            rows = conn.execute(text("SELECT id::text,plan_date,summary,created_at FROM itineraries WHERE user_id=:user_id ORDER BY created_at DESC LIMIT 50"), {"user_id": str(owner_id)}).mappings().all()
        return {"itineraries": [{**dict(row), "created_at": row["created_at"].isoformat()} for row in rows]}
    except SQLAlchemyError as exc:
        database_unavailable(exc)

@app.get("/api/itineraries/saved/{itinerary_id}")
async def load_saved(itinerary_id: UUID, owner_id: UUID = Query()):
    engine = database_engine()
    try:
        with engine.connect() as conn:
            row = conn.execute(text("SELECT request FROM itineraries WHERE id=:id AND user_id=:owner_id"), {"id": itinerary_id, "owner_id": str(owner_id)}).mappings().first()
            if not row: raise HTTPException(404, "Saved itinerary not found.")
            request = PlanRequest.model_validate(row["request"])
            stops = conn.execute(text("SELECT provider,provider_place_id,category FROM itinerary_stops WHERE itinerary_id=:id ORDER BY position"), {"id": itinerary_id}).mappings().all()
        if any(saved["provider"] not in ("openstreetmap_overpass", "openstreetmap") for saved in stops):
            raise HTTPException(409, "This saved itinerary uses the retired Google Places provider and cannot be refreshed by the current open-data setup. Create a new itinerary to save OSM place IDs.")
        refreshed_places = await place_details_many([saved["provider_place_id"] for saved in stops])
        places = []
        for saved in stops:
            place = refreshed_places.get(saved["provider_place_id"])
            if not place:
                raise HTTPException(404, "A saved OpenStreetMap place could not be refreshed; its map object may have changed or been removed.")
            place["category"] = saved["category"]
            if not location_in_supported_area(place.get("location")):
                raise HTTPException(422, "A saved stop is outside the configured Pondicherry area or lacks verified coordinates; it cannot be refreshed as a supported itinerary.")
            places.append(place)
        starting_point = await resolve_origin(request.start_location)
        origin = starting_point.copy()
        mode = {"drive": "DRIVE", "walk": "WALK", "bicycle": "BICYCLE", "transit": "TRANSIT"}[request.transport]
        if mode == "TRANSIT":
            raise HTTPException(422, "Transit routing is not available from the configured open routing provider. Choose drive, walk, or bicycle.")
        legs = []
        for place in places:
            legs.append(await route(origin, place["location"], mode))
            origin = place["location"]
        plan = build_plan(request.model_dump(), places, legs)
        try:
            weather_center = supported_area()["center"]
            plan["weather"] = await weather(weather_center["lat"], weather_center["lng"], request.date)
        except ProviderError:
            plan["weather"] = None
            plan["weather_advisory"] = "Weather data is temporarily unavailable; outdoor conditions are unknown."
        return {"itinerary": plan, "request": request.model_dump(), "refreshed_at": datetime.now(timezone.utc).isoformat()}
    except ProviderError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        database_unavailable(exc)

@app.delete("/api/itineraries/saved/{itinerary_id}")
async def delete_saved(itinerary_id: UUID, owner_id: UUID = Query()):
    engine = database_engine()
    try:
        with engine.begin() as conn:
            result = conn.execute(text("DELETE FROM itineraries WHERE id=:id AND user_id=:owner_id"), {"id": itinerary_id, "owner_id": str(owner_id)})
        if not result.rowcount: raise HTTPException(404, "Saved itinerary not found.")
        return {"deleted": True, "id": str(itinerary_id)}
    except SQLAlchemyError as exc:
        database_unavailable(exc)
