from datetime import datetime, timedelta


def estimate_visit_minutes(category: str) -> int:
    return 60 if category in ("beach", "nature", "heritage", "activity", "events") else 45


def classify_place_cost(place: dict) -> dict:
    price_range = place.get("price_range")
    if isinstance(price_range, dict) and price_range.get("currency") and (price_range.get("start") or price_range.get("end")):
        return {"status": "provider_range", "price_range": price_range,
                "note": "Provider-reported venue range; not a per-person or itinerary total."}
    estimate = place.get("cost_estimate")
    if isinstance(estimate, dict) and estimate.get("status") == "estimated" and estimate.get("source") and estimate.get("amount") is not None and estimate.get("currency"):
        return {"status": "estimated", "estimate": estimate,
                "note": "An estimate from the identified source; not a confirmed price."}
    return {"status": "unknown", "price_range": None, "estimate": None,
            "note": "No usable venue price information was returned."}


def assess_place_constraints(place: dict, request: dict) -> dict:
    checks = []
    rejected = False

    for preference in request.get("dietary_preferences", []):
        if preference == "none":
            continue
        evidence = (place.get("dietary_options") or {}).get(preference)
        if preference == "vegetarian" and evidence is None:
            evidence = place.get("serves_vegetarian_food")
        status = "provider_confirmed" if evidence is True else "provider_reports_unavailable" if evidence is False else "unknown"
        if evidence is False:
            rejected = True
        detail = (f"OpenStreetMap tags confirm {preference.replace('_', ' ')} suitability" if evidence is True else
                  f"OpenStreetMap tags report no {preference.replace('_', ' ')} suitability" if evidence is False else
                  f"No reliable provider field is available for {preference.replace('_', ' ')} suitability")
        checks.append({"constraint": preference, "status": status, "detail": detail})

    accessibility = place.get("accessibility_options") or {}
    access_map = {
        "wheelchair": ("wheelchairAccessibleEntrance", "accessible entrance"),
        "step_free": ("wheelchairAccessibleEntrance", "accessible entrance only; paths within the venue are not verified"),
        "accessible_restroom": ("wheelchairAccessibleRestroom", "accessible restroom"),
    }
    for need in request.get("accessibility_needs", []):
        if need in access_map:
            field, label = access_map[need]
            evidence = accessibility.get(field)
            status = "provider_confirmed" if evidence is True else "provider_reports_unavailable" if evidence is False else "unknown"
            if evidence is False:
                rejected = True
            detail = f"Provider confirms {label}" if evidence is True else f"Provider reports no {label}" if evidence is False else f"Provider has not supplied {label} information"
        else:
            status = "unknown"
            detail = "No reliable provider field verifies walking distance or low-walking suitability"
        checks.append({"constraint": need, "status": status, "detail": detail})

    group_requirement = None
    if request.get("group_type") == "family":
        group_requirement = ("good_for_children", "family/children suitability")
    elif request.get("group_type") in ("friends", "mixed") and request.get("group_size", 1) >= 6:
        group_requirement = ("good_for_groups", "large-group suitability")
    if group_requirement:
        field, label = group_requirement
        evidence = place.get(field)
        status = "provider_confirmed" if evidence is True else "provider_reports_unavailable" if evidence is False else "unknown"
        if evidence is False:
            rejected = True
        detail = f"Provider confirms {label}" if evidence is True else f"Provider reports unsuitable for {label}" if evidence is False else f"Provider has not supplied {label} information"
        checks.append({"constraint": request.get("group_type"), "status": status, "detail": detail})

    unknown_count = sum(check["status"] == "unknown" for check in checks)
    overall = "not_eligible" if rejected else "unknown" if unknown_count else "provider_confirmed_for_checked_fields"
    return {"eligible": not rejected, "overall": overall, "checks": checks, "unknown_count": unknown_count}


def meal_period_at(arrival: datetime) -> str | None:
    minute = arrival.hour * 60 + arrival.minute
    if 7 * 60 <= minute < 10 * 60:
        return "breakfast"
    if 12 * 60 <= minute < 15 * 60:
        return "lunch"
    if 18 * 60 <= minute < 21 * 60:
        return "dinner"
    return None


def weather_risk(weather_data: dict | None) -> tuple[bool | None, str]:
    if not weather_data or weather_data.get("freshness") != "forecast":
        return None, "Forecast is unavailable for the outing date; weather suitability is unknown."
    code = weather_data.get("weather_code")
    precipitation = weather_data.get("precipitation_probability_max")
    weather_codes_with_precipitation = set(range(51, 68)) | set(range(71, 78)) | set(range(80, 83)) | set(range(85, 87)) | set(range(95, 100))
    risky = code in weather_codes_with_precipitation or (precipitation is not None and precipitation >= 60)
    return risky, "Forecast shows precipitation risk; consider verified indoor alternatives." if risky else "Forecast does not currently indicate a high precipitation risk."


def indoor_alternatives(discovered_places: list[dict], selected_place_ids: set[str]) -> list[dict]:
    indoor_types = {"museum", "art_gallery", "movie_theater", "aquarium", "library", "shopping_mall", "performing_arts_theater"}
    results = []
    for place in discovered_places:
        kinds = set(place.get("types") or [])
        primary = place.get("primary_type")
        matched = sorted(kinds & indoor_types)
        if primary in indoor_types and primary not in matched:
            matched.insert(0, primary)
        if place.get("id") in selected_place_ids or not matched:
            continue
        results.append({"place": place, "indoor_type_evidence": matched,
                        "note": "Provider type suggests an indoor venue; confirm current conditions with the venue."})
    return results[:5]


def is_open_for_visit(place: dict, arrival: datetime, duration_minutes: int) -> bool | None:
    """Use provider regular hours when available; None means status is unknown."""
    periods = place.get("opening_periods")
    if not periods:
        return None
    arrival_week_minute = ((arrival.weekday() + 1) % 7) * 1440 + arrival.hour * 60 + arrival.minute
    week = 7 * 1440
    for period in periods:
        try:
            open_day = int(period["open_day"])
            close_day = int(period["close_day"])
            open_at = open_day * 1440 + int(period["open_time"][:2]) * 60 + int(period["open_time"][2:])
            if period.get("close_time"):
                close_at = close_day * 1440 + int(period["close_time"][:2]) * 60 + int(period["close_time"][2:])
                if close_at <= open_at:
                    close_at += week if close_day < open_day else 1440
            else:
                close_at = open_at + 1440
        except (KeyError, TypeError, ValueError):
            continue
        for occurrence in (-week, 0, week):
            interval_start = open_at + occurrence
            interval_end = close_at + occurrence
            if interval_start <= arrival_week_minute and arrival_week_minute + duration_minutes <= interval_end:
                return True
    return False


def build_plan(request: dict, places: list[dict], legs: list[dict]) -> dict:
    """Schedule discovered places against route estimates and provider regular hours."""
    deadline = request["duration_hours"] * 60
    elapsed = 0
    total_distance = 0
    total_travel = 0
    stops = []
    seen = set()
    start = datetime.strptime(request["date"] + " " + request["start_time"], "%Y-%m-%d %H:%M")
    unknown_hours = False
    for place_index, place in enumerate(places):
        if place["category"] in seen:
            continue
        leg = legs[place_index] if len(legs) > place_index else None
        if leg is None or leg.get("duration") is None or leg.get("distance_meters") is None:
            continue
        try:
            travel = max(0, (int(leg["duration"].rstrip("s")) + 59) // 60)
        except (ValueError, AttributeError):
            continue
        duration = estimate_visit_minutes(place["category"])
        if elapsed + travel + duration > deadline:
            continue
        arrival = start + timedelta(minutes=elapsed + travel)
        open_state = is_open_for_visit(place, arrival, duration)
        if open_state is False:
            continue
        unknown_hours |= open_state is None
        elapsed += travel + duration
        total_travel += travel
        total_distance += leg["distance_meters"]
        arrival_time = start + timedelta(minutes=elapsed - duration)
        meal_period = meal_period_at(arrival_time) if place["category"] in ("cafe", "restaurant") else None
        if meal_period:
            meal_service = place.get(f"serves_{meal_period}")
            meal_status = "provider reports meal service" if meal_service is True else "provider reports no meal service" if meal_service is False else "meal service is unverified"
        else:
            meal_status = None
        hours_note = "provider hours match this visit window" if open_state else "planned-time opening status is unknown"
        constraint_info = assess_place_constraints(place, request)
        cost_info = classify_place_cost(place)
        stops.append({"place": place, "start_minute_offset": elapsed - duration, "duration_minutes": duration, "travel_minutes": travel,
                      "travel_distance_meters": leg["distance_meters"], "planned_open": open_state,
                      "reason": f"Matches your {place['category']} interest; {hours_note}.", "estimated_cost_inr": None,
                      "constraint_assessment": constraint_info, "cost_info": cost_info,
                      "meal_period": meal_period, "meal_service_status": meal_status,
                      "route_geometry": leg.get("geometry")})
        seen.add(place["category"])
        if len(stops) >= 6:
            break
    for stop in stops:
        stop["start_time"] = (start + timedelta(minutes=stop["start_minute_offset"])).isoformat()
    known_prices = []
    estimated_costs = []
    unknown_costs = []
    for stop in stops:
        entry = {"place_id": stop["place"]["id"], "place_name": stop["place"]["name"]}
        if stop["cost_info"]["status"] == "provider_range":
            known_prices.append({**entry, **stop["cost_info"]})
        elif stop["cost_info"]["status"] == "estimated":
            estimated_costs.append({**entry, **stop["cost_info"]})
        else:
            unknown_costs.append({**entry, "reason": stop["cost_info"]["note"]})
    unknown_constraints = [check for stop in stops for check in stop["constraint_assessment"]["checks"] if check["status"] == "unknown"]
    food_stops = [stop for stop in stops if stop["place"]["category"] in ("cafe", "restaurant")]
    meal_windows = {meal_period_at(start + timedelta(minutes=minute)) for minute in range(0, elapsed + 1, 30)} - {None}
    missing_meal_windows = sorted(meal_windows - {stop["meal_period"] for stop in food_stops if stop["meal_period"]})
    warnings = ["Trip total and budget fit are unverified. Venue price ranges may not represent per-person spend; transport, taxes, tickets and add-ons are not priced.",
                "Regular opening hours do not guarantee admission and may not reflect special closures.",
                "Visit durations are planning estimates, not venue-provided values."]
    if unknown_hours:
        warnings.append("At least one stop has no regular opening-hours data, so its planned-time status is unknown.")
    if unknown_constraints:
        warnings.append("Some dietary, group or accessibility requirements lack provider evidence; those stops are not confirmed suitable.")
    if missing_meal_windows:
        warnings.append(f"The outing overlaps {', '.join(missing_meal_windows)} time; no cafe or restaurant stop is scheduled for that period.")
    warnings.append("Meal-period labels are time-window hints; menu, meal service and reservation availability are not guaranteed.")
    cost_summary = {"budget_target_inr": request["budget_inr"], "known_provider_price_ranges": known_prices,
                    "estimated_costs": estimated_costs, "unknown_costs": unknown_costs + ([{"item": "transport", "reason": "No transport price provider is configured."}] if total_travel else []),
                    "trip_total": None, "budget_assessment": "unverified"}
    return {"stops": stops, "total_duration_minutes": elapsed, "travel_minutes": total_travel, "distance_meters": total_distance,
            "budget_inr": request["budget_inr"], "estimated_total_inr": None,
            "budget_status": "unverified: available venue ranges do not establish total itinerary spend", "cost_summary": cost_summary,
            "constraint_status": "partially_verified" if unknown_constraints else "provider_confirmed_for_reported_fields",
            "warnings": warnings}
                                                                                                            