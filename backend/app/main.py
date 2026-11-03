from __future__ import annotations

import math
import os
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field, field_validator


class Coordinates(BaseModel):
    x: float = Field(ge=0, le=100)
    y: float = Field(ge=0, le=100)


class PlaceInput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    name: str
    category: str
    area: str
    rating: float = Field(ge=0, le=5)
    trendingScore: int = Field(ge=0, le=100)
    isOpenNow: bool
    openHours: str
    avgCostForTwo: float = Field(ge=0)
    tags: list[str] = Field(default_factory=list)
    hasOffer: bool = False
    offerLabel: str | None = None
    image: str = ""
    imageAlt: str = ""
    cuisine: str | None = None
    coordinates: Coordinates


class GroupMember(BaseModel):
    id: str
    name: str
    cuisine: str
    flavorPreference: Literal["spicy", "sweet", "mild", "any"]
    dietaryRestriction: str = "None"


class PlannerFormData(BaseModel):
    groupSize: int = Field(ge=1, le=20)
    budget: int = Field(ge=200, le=50_000)
    duration: int = Field(ge=1, le=24)
    cuisine: str
    flavorPreference: Literal["spicy", "sweet", "mild", "any"]
    transportMethod: Literal["walk", "auto", "cab", "bike", "own_vehicle"]
    latestReturnTime: str
    groupMode: bool = False
    groupMembers: list[GroupMember] = Field(default_factory=list)

    @field_validator("latestReturnTime")
    @classmethod
    def validate_return_time(cls, value: str) -> str:
        parts = value.split(":")
        if len(parts) != 2 or not all(part.isdigit() for part in parts):
            raise ValueError("Use 24-hour time in HH:MM format")
        hour, minute = map(int, parts)
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError("Use a valid 24-hour time")
        return value


class ItineraryRequest(BaseModel):
    formData: PlannerFormData
    places: list[PlaceInput] = Field(min_length=1, max_length=500)


class ItineraryStop(BaseModel):
    id: str
    placeId: str
    placeName: str
    category: str
    area: str
    estimatedCost: int
    costCategory: str
    duration: int
    travelTimeFromPrev: int
    travelCost: int
    openHours: str
    isOpenNow: bool
    hasOffer: bool
    offerLabel: str | None = None
    rating: float
    image: str
    imageAlt: str
    notes: str
    reservationAvailable: bool


class CostBreakdown(BaseModel):
    transport: int
    food: int
    entertainment: int
    shopping: int
    total: int


class GeneratedItinerary(BaseModel):
    id: str
    title: str
    stops: list[ItineraryStop]
    costBreakdown: CostBreakdown
    budget: int
    totalDuration: int
    startTime: str
    endTime: str
    surplus: int
    coveragePercent: int


TRANSPORT_RULES = {
    "walk": (4.5, 0, 0),
    "auto": (18, 18, 40),
    "cab": (24, 24, 80),
    "bike": (22, 10, 30),
    "own_vehicle": (24, 12, 20),
}
ROUTE_CENTER = Coordinates(x=50, y=50)
MAX_STOPS = 5

app = FastAPI(title="FEASTYmap API", version="1.0.0")

configured_origins = [
    origin.strip()
    for origin in os.getenv(
        "FRONTEND_ORIGINS", "http://localhost:4028,http://127.0.0.1:4028"
    ).split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=configured_origins,
    allow_origin_regex=r"https?://192\.168\.\d{1,3}\.\d{1,3}:4028",
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


def _distance_km(origin: Coordinates, destination: Coordinates) -> float:
    distance = math.hypot(destination.x - origin.x, destination.y - origin.y) * 0.25
    return max(0.5, distance)


def _round_amount(value: float) -> int:
    return math.floor(value + 0.5)


def _travel_for_leg(method: str, origin: Coordinates, destination: Coordinates) -> tuple[int, int]:
    speed_kph, cost_per_km, minimum_fare = TRANSPORT_RULES[method]
    distance = _distance_km(origin, destination)
    minutes = max(5, math.ceil((distance / speed_kph) * 60))
    cost = 0 if method == "walk" else _round_amount(max(minimum_fare, distance * cost_per_km))
    return minutes, cost


def _matches_dietary_restriction(place: PlaceInput, restriction: str) -> bool:
    normalized = restriction.lower().strip()
    if not normalized or normalized in {"none", "any"}:
        return True

    searchable = f"{place.name} {place.cuisine or ''} {' '.join(place.tags)}".lower()
    excluded_terms = {
        "vegan": ("chicken", "meat", "fish", "egg", "milk", "cheese", "dairy", "butter", "cream"),
        "vegetarian": ("chicken", "meat", "mutton", "fish", "seafood", "egg"),
        "veg": ("chicken", "meat", "mutton", "fish", "seafood", "egg"),
        "halal": ("bar", "cocktail", "pork"),
        "jain": ("meat", "fish", "egg", "onion", "garlic"),
    }
    for restriction_name, terms in excluded_terms.items():
        if restriction_name in normalized:
            return not any(term in searchable for term in terms)
    return True


def _preference_score(place: PlaceInput, data: PlannerFormData) -> float:
    members = (
        data.groupMembers
        if data.groupMode and data.groupMembers
        else [
            GroupMember(
                id="host",
                name="Host",
                cuisine=data.cuisine,
                flavorPreference=data.flavorPreference,
                dietaryRestriction="None",
            )
        ]
    )
    searchable = f"{place.name} {place.cuisine or ''} {' '.join(place.tags)}".lower()
    score = place.rating * 10 + place.trendingScore / 10
    for member in members:
        if member.cuisine.lower() == "any" or member.cuisine.lower() in searchable:
            score += 18
        if member.flavorPreference == "any" or member.flavorPreference in searchable:
            score += 10
        score += 8 if _matches_dietary_restriction(place, member.dietaryRestriction) else -100

    if place.category in {"restaurant", "cafe", "street_food"}:
        score += 8
    return score


def _cost_category(category: str) -> str:
    if category in {"cafe", "restaurant", "dessert", "street_food"}:
        return "food"
    if category == "mall":
        return "shopping"
    if category in {"bar", "event"}:
        return "entertainment"
    return "food"


def _stop_duration(category: str) -> int:
    if category == "mall":
        return 90
    if category in {"bar", "event"}:
        return 75
    return 45


def _format_time(total_minutes: int) -> str:
    minutes = total_minutes % 1440
    hour, minute = divmod(minutes, 60)
    suffix = "AM" if hour < 12 else "PM"
    return f"{hour % 12 or 12}:{minute:02d} {suffix}"


def generate_itinerary(request: ItineraryRequest) -> GeneratedItinerary | None:
    data = request.formData
    duration_limit = data.duration * 60
    candidates = [
        place
        for place in request.places
        if place.isOpenNow
        and all(
            _matches_dietary_restriction(place, member.dietaryRestriction)
            for member in data.groupMembers
        )
    ]
    candidates.sort(key=lambda place: _preference_score(place, data), reverse=True)

    selected: list[PlaceInput] = []
    previous = ROUTE_CENTER
    elapsed = 0
    transport_total = 0
    projected_total = 0

    for place in candidates:
        if len(selected) >= MAX_STOPS:
            break
        if place.category != "restaurant" and any(
            item.category == place.category for item in selected
        ):
            continue

        travel_minutes, travel_cost = _travel_for_leg(
            data.transportMethod, previous, place.coordinates
        )
        duration = _stop_duration(place.category)
        estimated_cost = _round_amount(place.avgCostForTwo * data.groupSize / 2)
        next_projected_total = projected_total + travel_cost + estimated_cost
        if elapsed + travel_minutes + duration > duration_limit or next_projected_total > data.budget:
            continue

        selected.append(place)
        elapsed += travel_minutes + duration
        transport_total += travel_cost
        projected_total = next_projected_total
        previous = place.coordinates

    if not selected:
        return None

    stops: list[ItineraryStop] = []
    previous = ROUTE_CENTER
    for index, place in enumerate(selected):
        travel_minutes, travel_cost = _travel_for_leg(
            data.transportMethod, previous, place.coordinates
        )
        category = _cost_category(place.category)
        stops.append(
            ItineraryStop(
                id=f"stop-{place.id}-{index}",
                placeId=place.id,
                placeName=place.name,
                category=place.category,
                area=place.area,
                estimatedCost=_round_amount(place.avgCostForTwo * data.groupSize / 2),
                costCategory=category,
                duration=_stop_duration(place.category),
                travelTimeFromPrev=travel_minutes,
                travelCost=travel_cost,
                openHours=place.openHours,
                isOpenNow=place.isOpenNow,
                hasOffer=place.hasOffer,
                offerLabel=place.offerLabel,
                rating=place.rating,
                image=place.image,
                imageAlt=place.imageAlt,
                notes=(
                    f"Selected for your {'group' if data.cuisine == 'Any' else data.cuisine} "
                    "preferences. Estimated travel and spend are included."
                ),
                reservationAvailable=place.category in {"restaurant", "bar", "cafe"},
            )
        )
        previous = place.coordinates

    food = sum(stop.estimatedCost for stop in stops if stop.costCategory == "food")
    entertainment = sum(
        stop.estimatedCost for stop in stops if stop.costCategory == "entertainment"
    )
    shopping = sum(stop.estimatedCost for stop in stops if stop.costCategory == "shopping")
    total = transport_total + food + entertainment + shopping
    total_duration = sum(stop.duration + stop.travelTimeFromPrev for stop in stops)
    return_hour, return_minute = map(int, data.latestReturnTime.split(":"))
    return_minutes = return_hour * 60 + return_minute
    areas = list(dict.fromkeys(stop.area for stop in stops))

    return GeneratedItinerary(
        id=f"itin-{uuid4().hex}",
        title=f"{data.duration}h Outing — {' → '.join(areas)}",
        stops=stops,
        costBreakdown=CostBreakdown(
            transport=transport_total,
            food=food,
            entertainment=entertainment,
            shopping=shopping,
            total=total,
        ),
        budget=data.budget,
        totalDuration=total_duration,
        startTime=_format_time(return_minutes - total_duration),
        endTime=_format_time(return_minutes),
        surplus=data.budget - total,
        coveragePercent=_round_amount(total / data.budget * 100),
    )


@app.get("/")
@app.get("/health")
@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "feastymap-api"}


@app.post("/api/itinerary/generate", response_model=GeneratedItinerary | None)
def create_itinerary(request: ItineraryRequest) -> GeneratedItinerary | None:
    return generate_itinerary(request)