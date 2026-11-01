import unittest
from app.planner import (assess_place_constraints, build_plan, classify_place_cost,
                         indoor_alternatives, is_open_for_visit, weather_risk)
from datetime import datetime

class PlannerTests(unittest.TestCase):
    def setUp(self):
        self.request = {"date":"2026-10-28", "start_time":"09:00", "duration_hours":4, "budget_inr":1000}
        self.places = [{"id":"live-1", "name":"Provider Cafe", "category":"cafe", "open_now":True, "price_level":"PRICE_LEVEL_INEXPENSIVE"},
                       {"id":"live-2", "name":"Provider Beach", "category":"beach", "open_now":True}]
        self.legs = [{"duration":"600s", "distance_meters":1500}, {"duration":"900s", "distance_meters":2500}]
    def test_only_provider_data_and_routes_are_scheduled(self):
        result = build_plan(self.request, self.places, self.legs)
        self.assertEqual([s["place"]["id"] for s in result["stops"]], ["live-1", "live-2"])
        self.assertEqual(result["travel_minutes"], 25)
        self.assertIsNone(result["estimated_total_inr"])
        self.assertEqual(result["stops"][0]["meal_period"], "breakfast")
        self.assertEqual(result["stops"][0]["meal_service_status"], "meal service is unverified")
    def test_closed_places_skipped(self):
        self.places[0]["opening_periods"] = [{"open_day":0,"open_time":"0800","close_day":0,"close_time":"1800"}]
        self.assertEqual(len(build_plan(self.request, self.places, self.legs)["stops"]), 1)

    def test_planned_hours_override_current_open_status(self):
        self.places[0]["open_now"] = False
        self.places[0]["opening_periods"] = [{"open_day":3,"open_time":"0900","close_day":3,"close_time":"1200"}]
        result = build_plan(self.request, self.places, self.legs)
        self.assertEqual(result["stops"][0]["place"]["id"], "live-1")
        self.assertTrue(result["stops"][0]["planned_open"])
    def test_overlong_stops_are_skipped(self):
        self.request["duration_hours"] = 0.5
        self.assertEqual(build_plan(self.request, self.places, self.legs)["stops"], [])
    def test_missing_route_is_not_faked(self):
        self.assertEqual(build_plan(self.request, self.places, [])["stops"], [])

    def test_costs_distinguish_provider_ranges_estimates_and_unknown(self):
        known = classify_place_cost({"price_range": {"start": {"amount": "100", "currency": "INR"}, "currency": "INR"}})
        estimate = classify_place_cost({"cost_estimate": {"status": "estimated", "amount": 250, "currency": "INR", "source": "local estimate"}})
        missing = classify_place_cost({"price_level": "PRICE_LEVEL_INEXPENSIVE"})
        self.assertEqual([known["status"], estimate["status"], missing["status"]], ["provider_range", "estimated", "unknown"])

    def test_unknown_costs_never_claim_budget_fit(self):
        result = build_plan(self.request, self.places, self.legs)
        self.assertEqual(result["cost_summary"]["budget_assessment"], "unverified")
        self.assertIsNone(result["cost_summary"]["trip_total"])
        self.assertTrue(result["cost_summary"]["unknown_costs"])

    def test_dietary_filter_only_rejects_verified_negative(self):
        request = {"dietary_preferences": ["vegetarian"]}
        self.assertFalse(assess_place_constraints({"serves_vegetarian_food": False}, request)["eligible"])
        self.assertEqual(assess_place_constraints({"serves_vegetarian_food": True}, request)["overall"], "provider_confirmed_for_checked_fields")
        unknown = assess_place_constraints({}, request)
        self.assertTrue(unknown["eligible"])
        self.assertEqual(unknown["overall"], "unknown")

    def test_accessibility_missing_data_stays_unknown(self):
        request = {"accessibility_needs": ["wheelchair"]}
        self.assertEqual(assess_place_constraints({}, request)["unknown_count"], 1)
        self.assertFalse(assess_place_constraints({"accessibility_options": {"wheelchairAccessibleEntrance": False}}, request)["eligible"])

    def test_opening_hour_conflict(self):
        place = {"opening_periods": [{"open_day": 3, "open_time": "0900", "close_day": 3, "close_time": "1000"}]}
        self.assertFalse(is_open_for_visit(place, datetime(2026, 10, 28, 9, 30), 60))

    def test_weather_risk_only_offers_real_indoor_places(self):
        self.assertTrue(weather_risk({"freshness": "forecast", "weather_code": 61})[0])
        self.assertIsNone(weather_risk({"freshness": "date outside available forecast"})[0])
        candidates = indoor_alternatives([{"id": "real", "types": ["museum"]}, {"id": "outside", "types": ["beach"]}], {"selected"})
        self.assertEqual([item["place"]["id"] for item in candidates], ["real"])
        self.assertEqual(indoor_alternatives([], set()), [])

if __name__ == "__main__": unittest.main()
