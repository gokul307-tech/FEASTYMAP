import unittest
from pydantic import ValidationError

from app.main import ItineraryRequest, app, generate_itinerary, health


def make_request(*, budget: int = 2_000, transport: str = "auto") -> ItineraryRequest:
    return ItineraryRequest.model_validate(
        {
            "formData": {
                "groupSize": 2,
                "budget": budget,
                "duration": 4,
                "cuisine": "Any",
                "flavorPreference": "any",
                "transportMethod": transport,
                "latestReturnTime": "22:00",
                "groupMode": False,
                "groupMembers": [],
            },
            "places": [
                {
                    "id": "place-test",
                    "name": "Test Cafe",
                    "category": "cafe",
                    "area": "Central",
                    "rating": 4.5,
                    "trendingScore": 80,
                    "isOpenNow": True,
                    "openHours": "8:00 AM - 10:00 PM",
                    "avgCostForTwo": 500,
                    "tags": ["Coffee"],
                    "hasOffer": False,
                    "coordinates": {"x": 50, "y": 50},
                }
            ],
        }
    )


class ItineraryGenerationTests(unittest.TestCase):
    def test_health_routes_are_available(self) -> None:
        registered_paths = {route.path for route in app.routes}
        self.assertTrue({"/", "/health", "/api/health"}.issubset(registered_paths))
        self.assertEqual(health()["status"], "ok")

    def test_generates_contract_shaped_itinerary_within_budget(self) -> None:
        itinerary = generate_itinerary(make_request())

        self.assertIsNotNone(itinerary)
        assert itinerary is not None
        self.assertEqual(itinerary.stops[0].placeId, "place-test")
        self.assertEqual(itinerary.costBreakdown.total, 540)
        self.assertLessEqual(itinerary.costBreakdown.total, itinerary.budget)
        self.assertEqual(itinerary.endTime, "10:00 PM")

    def test_returns_no_itinerary_when_nothing_fits_budget(self) -> None:
        self.assertIsNone(generate_itinerary(make_request(budget=200)))

    def test_rejects_unknown_transport_method(self) -> None:
        with self.assertRaises(ValidationError):
            make_request(transport="teleport")


if __name__ == "__main__":
    unittest.main()