import os
import unittest
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient
from app.main import app, _requests
from app.providers import ProviderError, discover_places, request_with_retries
from app.geography import supported_area
import httpx
from datetime import datetime, timezone

client = TestClient(app)

class ApiTests(unittest.TestCase):
    def test_health(self):
        response = client.get('/health')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'ok')

    def test_invalid_plan_is_rejected(self):
        response = client.post('/api/itineraries', json={"start_location":"Pondicherry", "date":"tomorrow", "start_time":"99:00", "duration_hours":99, "budget_inr":-1, "interests":[]})
        self.assertEqual(response.status_code, 422)

    def test_impossible_calendar_date_is_rejected(self):
        response = client.post('/api/itineraries', json={"start_location":"Pondicherry", "date":"2026-02-30", "start_time":"09:00", "duration_hours":3, "budget_inr":500, "interests":["beach"]})
        self.assertEqual(response.status_code, 422)

    def test_provider_missing_service_is_honest_error_without_credentials(self):
        with patch('app.providers._request_json', new=AsyncMock(side_effect=ProviderError('Photon is temporarily unavailable.'))):
            response = client.get('/api/places?category=beach')
        self.assertEqual(response.status_code, 503)
        self.assertIn('Photon', response.json()['detail'])

    def test_provider_failure_is_sanitized(self):
        with patch('app.main.discover_places', new=AsyncMock(side_effect=ProviderError('Overpass request failed; retry later.'))):
            response = client.get('/api/places?category=beach')
        self.assertEqual(response.status_code, 503)
        self.assertIn('retry later', response.json()['detail'])

    def test_place_search_returns_only_supported_locations(self):
        center = supported_area()["center"]
        provider_places = [
            {"id": "inside", "name": "Inside Pondicherry", "location": center},
            {"id": "outside", "name": "Karaikal result", "location": {"lat": 10.925, "lng": 79.838}},
            {"id": "unknown", "name": "No coordinates"},
        ]
        with patch('app.main.discover_places', new=AsyncMock(return_value=provider_places)):
            response = client.get('/api/places?category=cafe&query=White%20Town')
        self.assertEqual(response.status_code, 200)
        self.assertEqual([place['id'] for place in response.json()['places']], ['inside'])
        self.assertIn('provisional radius', response.json()['scope']['boundary_type'])

    def test_place_search_explains_unsupported_union_territory_regions(self):
        for region in ("Karaikal", "Mahe", "Yanam"):
            response = client.get(f'/api/places?category=cafe&query={region}')
            self.assertEqual(response.status_code, 422)
            self.assertIn('limited to Pondicherry city', response.json()['detail'])

    def test_places_adapter_queries_overpass_and_filters_provider_payload(self):
        async def check():
            elements = [
                {"type":"node","id":1,"lat":11.916064,"lon":79.812325,"tags":{"name":"Inside","amenity":"cafe"}},
                {"type":"node","id":2,"lat":10.925,"lon":79.838,"tags":{"name":"Distant","amenity":"cafe"}},
                {"type":"way","id":3,"center":{"lat":11.916,"lon":79.812},"tags":{"amenity":"cafe"}},
            ]
            with patch('app.providers._request_json', new=AsyncMock(return_value={"elements":elements})) as provider_request:
                found = await discover_places("Pondicherry", "cafe", 5)
            return found, provider_request.call_args.kwargs['data']['data']
        import asyncio
        found, query = asyncio.run(check())
        self.assertEqual([place['id'] for place in found], ['osm:node:1'])
        self.assertIn('around:10000,11.916064,79.812325', query)

    def test_transit_is_rejected_with_explanation(self):
        response = client.post('/api/itineraries', json={"start_location":"Pondicherry", "date":"2026-10-28", "start_time":"09:00", "duration_hours":3, "budget_inr":500, "interests":["beach"], "transport":"transit"})
        self.assertEqual(response.status_code, 422)
        self.assertIn('Transit routing is not available', response.json()['detail'])

    def test_place_details_reject_out_of_scope_and_unknown_coordinates(self):
        with patch('app.main.place_details', new=AsyncMock(return_value={"id":"remote","location":{"lat":10.925,"lng":79.838}})):
            response = client.get('/api/places/remote')
        self.assertEqual(response.status_code, 404)
        with patch('app.main.place_details', new=AsyncMock(return_value={"id":"unknown","location":{}})):
            response = client.get('/api/places/unknown')
        self.assertEqual(response.status_code, 404)

    def test_plan_skips_out_of_scope_provider_candidate_but_keeps_valid_place(self):
        inside = {"id":"inside","name":"Pondicherry Beach","category":"beach","location":supported_area()["center"]}
        outside = {"id":"outside","name":"Karaikal Beach","category":"beach","location":{"lat":10.925,"lng":79.838}}
        body = {"start_location":"Chennai","date":"2026-10-28","start_time":"09:00","duration_hours":4,"budget_inr":1000,"interests":["beach"]}
        with patch('app.main.discover_places', new=AsyncMock(return_value=[outside, inside])), \
             patch('app.main.resolve_origin', new=AsyncMock(return_value={"lat":13.08,"lng":80.27})), \
             patch('app.main.route', new=AsyncMock(return_value={"duration":"600s","distance_meters":1000})), \
             patch('app.main.weather', new=AsyncMock(return_value={"freshness":"date outside available forecast"})):
            response = client.post('/api/itineraries', json=body)
        self.assertEqual(response.status_code, 200)
        self.assertEqual([stop['place']['id'] for stop in response.json()['itinerary']['stops']], ['inside'])

    def test_plan_explains_when_only_out_of_scope_places_are_returned(self):
        outside = {"id":"kar","name":"Distant beach","category":"beach","location":{"lat":10.925,"lng":79.838}}
        body = {"start_location":"Pondicherry","date":"2026-10-28","start_time":"09:00","duration_hours":4,"budget_inr":1000,"interests":["beach"]}
        with patch('app.main.discover_places', new=AsyncMock(return_value=[outside])), \
             patch('app.main.resolve_origin', new=AsyncMock(return_value=supported_area()["center"])):
            response = client.post('/api/itineraries', json=body)
        self.assertEqual(response.status_code, 422)
        self.assertIn('outside the configured Pondicherry search area', response.json()['detail'])

    def test_replacement_refuses_out_of_scope_provider_candidate(self):
        body = {"request":{"start_location":"Pondicherry","date":"2026-10-28","start_time":"09:00","duration_hours":3,"budget_inr":500,"interests":["beach"]},
                "itinerary":{"stops":[{"place":{"id":"old","category":"beach","location":supported_area()["center"]}}]},
                "stop_index":0,"category":"beach","excluded_place_ids":["old"]}
        outside = {"id":"remote","name":"Karaikal beach","location":{"lat":10.925,"lng":79.838}}
        with patch('app.main.discover_places', new=AsyncMock(return_value=[outside])):
            response = client.post('/api/itineraries/replace-stop', json=body)
        self.assertEqual(response.status_code, 404)
        self.assertIn('in-scope Pondicherry alternative', response.json()['detail'])

    def test_saved_plans_require_database_configuration(self):
        with patch.dict(os.environ, {}, clear=True):
            response = client.get('/api/itineraries/saved?owner_id=2f1a7ea4-a1b6-4b87-b9f0-229ce836947b')
        self.assertEqual(response.status_code, 503)
        self.assertIn('DATABASE_URL', response.json()['detail'])

    def test_stop_replacement_reports_no_suitable_candidate(self):
        body = {"request":{"start_location":"Pondicherry", "date":"2026-10-28", "start_time":"09:00", "duration_hours":3, "budget_inr":500, "interests":["beach"]},
                "itinerary":{"stops":[{"place":{"id":"old","category":"beach"}}]}, "stop_index":0, "category":"beach", "excluded_place_ids":["old"]}
        with patch('app.main.discover_places', new=AsyncMock(return_value=[])):
            response = client.post('/api/itineraries/replace-stop', json=body)
        self.assertEqual(response.status_code, 404)
        self.assertIn('unchanged', response.json()['detail'])

    def test_plan_endpoint_uses_provider_routes_and_forecast(self):
        place = {"id":"live-place","name":"Live Beach","category":"beach","location":{"lat":11.9,"lng":79.8},"open_now":True,"retrieved_at":"2026-10-27T00:00:00Z"}
        body = {"start_location":"Pondicherry","date":"2026-10-28","start_time":"09:00","duration_hours":4,"budget_inr":1200,"interests":["beach"],
                "transport":"walk","group_size":3,"group_type":"family","dietary_preferences":["vegetarian"],"accessibility_needs":["low_walking"]}
        with patch('app.main.discover_places', new=AsyncMock(return_value=[place])), \
             patch('app.main.resolve_origin', new=AsyncMock(return_value={"lat":11.8,"lng":79.7})), \
             patch('app.main.route', new=AsyncMock(return_value={"duration":"600s","distance_meters":1500})), \
             patch('app.main.weather', new=AsyncMock(return_value={"source":"Open-Meteo","date":"2026-10-28","freshness":"forecast","precipitation_probability_max":10})):
            response = client.post('/api/itineraries', json=body)
        self.assertEqual(response.status_code, 200)
        plan = response.json()['itinerary']
        self.assertEqual(plan['stops'][0]['place']['id'], 'live-place')
        self.assertEqual(plan['travel_minutes'], 10)
        self.assertEqual(plan['weather']['source'], 'Open-Meteo')
        self.assertIsNone(plan['estimated_total_inr'])

    def test_plan_suggests_only_discovered_indoor_options_for_rain(self):
        beach = {"id":"beach","name":"Beach","category":"beach","location":{"lat":11.9,"lng":79.8},"types":["beach"]}
        museum = {"id":"museum","name":"Museum","category":"beach","location":{"lat":11.91,"lng":79.81},"types":["museum"]}
        body = {"start_location":"Pondicherry","date":"2026-10-28","start_time":"09:00","duration_hours":4,"budget_inr":1000,"interests":["beach"]}
        with patch('app.main.discover_places', new=AsyncMock(return_value=[beach, museum])), \
             patch('app.main.resolve_origin', new=AsyncMock(return_value={"lat":11.8,"lng":79.7})), \
             patch('app.main.route', new=AsyncMock(return_value={"duration":"600s","distance_meters":1000})), \
             patch('app.main.weather', new=AsyncMock(return_value={"freshness":"forecast","weather_code":61,"precipitation_probability_max":80})):
            response = client.post('/api/itineraries', json=body)
        self.assertEqual(response.status_code, 200)
        plan = response.json()['itinerary']
        self.assertEqual([x['place']['id'] for x in plan['weather_alternatives']], ['museum'])
        self.assertIn('precipitation risk', plan['weather_advisory'])

    def test_no_constraint_suitable_place_returns_clear_explanation(self):
        place = {"id":"place","name":"Not vegetarian","category":"restaurant","location":{"lat":11.9,"lng":79.8},"serves_vegetarian_food":False}
        body = {"start_location":"Pondicherry","date":"2026-10-28","start_time":"09:00","duration_hours":4,"budget_inr":1000,"interests":["restaurant"],"dietary_preferences":["vegetarian"]}
        with patch('app.main.discover_places', new=AsyncMock(return_value=[place])), \
             patch('app.main.resolve_origin', new=AsyncMock(return_value={"lat":11.8,"lng":79.7})):
            response = client.post('/api/itineraries', json=body)
        self.assertEqual(response.status_code, 422)
        self.assertIn('Provider data conflicts', response.json()['detail'])

    def test_request_body_limit(self):
        response = client.post('/api/itineraries', content=b'x' * 65537, headers={'Content-Type':'application/json'})
        self.assertEqual(response.status_code, 413)

    def test_rate_limit(self):
        _requests.clear()
        responses = [client.get('/health') for _ in range(41)]
        self.assertEqual(responses[-1].status_code, 429)
        _requests.clear()

    def test_saved_itinerary_writes_only_user_request_and_provider_ids(self):
        class Result:
            def mappings(self): return self
            def one(self): return {"id":"saved-id", "created_at":datetime(2026,10,27,tzinfo=timezone.utc)}
        class Connection:
            def execute(self, statement, params):
                executed.append((str(statement), params))
                return Result()
        class Context:
            def __enter__(self): return Connection()
            def __exit__(self, *_): return False
        class Engine:
            def begin(self): return Context()
        executed = []
        body = {"owner_id":"2f1a7ea4-a1b6-4b87-b9f0-229ce836947b", "request":{"start_location":"Pondicherry", "date":"2026-10-28", "start_time":"09:00", "duration_hours":3, "budget_inr":500, "interests":["beach"]},
                "itinerary":{"total_duration_minutes":120, "travel_minutes":20, "distance_meters":4000, "stops":[{"start_time":"2026-10-28T09:20:00","duration_minutes":60,"place":{"id":"ChIJproviderid","name":"External name","location":supported_area()["center"]}}]}}
        with patch('app.main.database_engine', return_value=Engine()):
            response = client.post('/api/itineraries/saved', json=body)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(executed), 2)
        self.assertNotIn('External name', executed[0][1]['summary'])
        self.assertEqual(executed[1][1]['place_id'], 'ChIJproviderid')

    def test_saved_itinerary_rejects_out_of_scope_and_missing_locations(self):
        body = {"owner_id":"2f1a7ea4-a1b6-4b87-b9f0-229ce836947b",
                "request":{"start_location":"Pondicherry","date":"2026-10-28","start_time":"09:00","duration_hours":3,"budget_inr":500,"interests":["beach"]},
                "itinerary":{"stops":[{"place":{"id":"remote","location":{"lat":10.925,"lng":79.838}}}]}}
        response = client.post('/api/itineraries/saved', json=body)
        self.assertEqual(response.status_code, 422)
        body['itinerary']['stops'][0]['place']['location'] = None
        response = client.post('/api/itineraries/saved', json=body)
        self.assertEqual(response.status_code, 422)

    def test_saved_itinerary_listing(self):
        class Result:
            def mappings(self): return self
            def all(self): return [{"id":"saved-id", "plan_date":"2026-10-28", "summary":{"travel_minutes":20}, "created_at":datetime(2026,10,27,tzinfo=timezone.utc)}]
        class Connection:
            def execute(self, *_): return Result()
        class Context:
            def __enter__(self): return Connection()
            def __exit__(self, *_): return False
        class Engine:
            def connect(self): return Context()
        with patch('app.main.database_engine', return_value=Engine()):
            response = client.get('/api/itineraries/saved?owner_id=2f1a7ea4-a1b6-4b87-b9f0-229ce836947b')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['itineraries'][0]['summary']['travel_minutes'], 20)

    def test_saved_itinerary_delete(self):
        class Result: rowcount = 1
        class Connection:
            def execute(self, *_): return Result()
        class Context:
            def __enter__(self): return Connection()
            def __exit__(self, *_): return False
        class Engine:
            def begin(self): return Context()
        with patch('app.main.database_engine', return_value=Engine()):
            response = client.delete('/api/itineraries/saved/2f1a7ea4-a1b6-4b87-b9f0-229ce836947b?owner_id=2f1a7ea4-a1b6-4b87-b9f0-229ce836947b')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['deleted'])

    def test_saved_google_itinerary_is_preserved_but_not_refreshed(self):
        saved_request = {"start_location":"Pondicherry", "date":"2026-10-28", "start_time":"09:00", "duration_hours":3, "budget_inr":500, "interests":["beach"]}
        class Rows:
            def mappings(self): return self
            def first(self): return {"request":saved_request}
        class StopRows:
            def mappings(self): return self
            def all(self): return [{"provider":"google_places", "provider_place_id":"ChIJsaved", "category":"beach"}]
            def scalars(self): return self
        class Connection:
            count = 0
            def execute(self, *_):
                self.count += 1
                return Rows() if self.count == 1 else StopRows()
        class Context:
            def __enter__(self): return Connection()
            def __exit__(self, *_): return False
        class Engine:
            def connect(self): return Context()
        with patch('app.main.database_engine', return_value=Engine()):
            response = client.get('/api/itineraries/saved/2f1a7ea4-a1b6-4b87-b9f0-229ce836947b?owner_id=2f1a7ea4-a1b6-4b87-b9f0-229ce836947b')
        self.assertEqual(response.status_code, 409)
        self.assertIn('retired Google Places provider', response.json()['detail'])

    def test_stop_replacement_recalculates_and_keeps_other_stops(self):
        body = {"request":{"start_location":"Pondicherry", "date":"2026-10-28", "start_time":"09:00", "duration_hours":3, "budget_inr":500, "interests":["beach"]},
                "itinerary":{"stops":[{"place":{"id":"old","category":"beach","location":{"lat":11.9,"lng":79.8}}}, {"place":{"id":"keep","category":"cafe","location":{"lat":11.91,"lng":79.81}}}]},
                "stop_index":0, "category":"beach", "excluded_place_ids":["old","keep"]}
        alternative = {"id":"new","name":"Live beach","category":"beach","location":{"lat":11.92,"lng":79.82},"open_now":True}
        with patch('app.main.discover_places', new=AsyncMock(return_value=[alternative])), \
             patch('app.main.resolve_origin', new=AsyncMock(return_value={"lat":11.9,"lng":79.8})), \
             patch('app.main.route', new=AsyncMock(return_value={"duration":"300s","distance_meters":1000})):
            response = client.post('/api/itineraries/replace-stop', json=body)
        self.assertEqual(response.status_code, 200)
        self.assertEqual([s['place']['id'] for s in response.json()['itinerary']['stops']], ['new','keep'])
        self.assertEqual(response.json()['itinerary']['travel_minutes'], 10)

    def test_stop_replacement_tries_next_candidate_when_first_is_closed_at_visit_time(self):
        body = {"request":{"start_location":"Pondicherry", "date":"2026-10-28", "start_time":"09:00", "duration_hours":3, "budget_inr":500, "interests":["beach"]},
                "itinerary":{"stops":[{"place":{"id":"old","category":"beach","location":{"lat":11.9,"lng":79.8}}, "duration_minutes":60,"travel_minutes":5,"travel_distance_meters":1000}]},
                "stop_index":0, "category":"beach", "excluded_place_ids":["old"]}
        closed = {"id":"closed","name":"Closed for planned time","category":"beach","location":{"lat":11.91,"lng":79.81},"opening_periods":[{"open_day":4,"open_time":"0900","close_day":4,"close_time":"1700"}]}
        open_place = {"id":"open","name":"Open for planned time","category":"beach","location":{"lat":11.92,"lng":79.82},"opening_periods":[{"open_day":3,"open_time":"0900","close_day":3,"close_time":"1700"}]}
        with patch('app.main.discover_places', new=AsyncMock(return_value=[closed,open_place])), \
             patch('app.main.resolve_origin', new=AsyncMock(return_value={"lat":11.8,"lng":79.7})), \
             patch('app.main.route', new=AsyncMock(return_value={"duration":"300s","distance_meters":1000})):
            response = client.post('/api/itineraries/replace-stop', json=body)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['itinerary']['stops'][0]['place']['id'], 'open')

    def test_provider_retry_is_bounded_and_recovers(self):
        async def check():
            calls = 0
            def handler(request):
                nonlocal calls
                calls += 1
                return httpx.Response(503 if calls == 1 else 200, request=request)
            transport = httpx.MockTransport(handler)
            async with httpx.AsyncClient(transport=transport) as provider_client:
                with patch('app.providers.asyncio.sleep', new=AsyncMock()):
                    response = await request_with_retries(provider_client, 'GET', 'https://provider.test')
            return response.status_code, calls
        import asyncio
        status, calls = asyncio.run(check())
        self.assertEqual((status, calls), (200, 2))

if __name__ == '__main__': unittest.main()

