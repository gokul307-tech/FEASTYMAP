from datetime import date as calendar_date, datetime
from typing import Literal
from pydantic import BaseModel, Field, field_validator
from uuid import UUID

Category = Literal["beach", "cafe", "restaurant", "heritage", "temple", "shopping", "nature", "activity", "events"]

class PlanRequest(BaseModel):
    start_location: str = Field(min_length=2, max_length=180)
    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    start_time: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    duration_hours: float = Field(ge=1, le=14)
    budget_inr: int = Field(ge=0, le=1_000_000)
    interests: list[Category] = Field(min_length=1, max_length=4)
    transport: Literal["walk", "bicycle", "drive", "transit"] = "drive"
    group_size: int = Field(default=2, ge=1, le=30)
    dietary_preferences: list[Literal["vegetarian", "vegan", "halal", "jain", "gluten_free", "none"]] = []
    accessibility_needs: list[Literal["step_free", "wheelchair", "low_walking", "accessible_restroom"]] = []
    group_type: Literal["solo", "couple", "family", "friends", "seniors", "mixed"] = "mixed"

    @field_validator("date")
    @classmethod
    def valid_calendar_date(cls, value: str) -> str:
        calendar_date.fromisoformat(value)
        return value

class Place(BaseModel):
    id: str
    name: str
    address: str | None = None
    category: str
    location: dict[str, float]
    rating: float | None = None
    open_now: bool | None = None
    opening_hours: list[str] = []
    price_level: str | None = None
    provider: str = "openstreetmap_overpass"
    osm_uri: str | None = None
    dietary_options: dict[str, bool] = {}
    source: str = "OpenStreetMap contributors via Overpass API"
    retrieved_at: datetime
    freshness: str = "OpenStreetMap data; update time varies by mapped object"
    verification: str = "Contributor-mapped; venue status and details may be incomplete"

class SavedPlanRequest(BaseModel):
    owner_id: UUID
    request: PlanRequest
    itinerary: dict

class ReplaceStopRequest(BaseModel):
    request: PlanRequest
    itinerary: dict
    stop_index: int = Field(ge=0, le=5)
    category: Category
    excluded_place_ids: list[str] = Field(default=[], max_length=20)

