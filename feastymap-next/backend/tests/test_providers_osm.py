import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from app.geography import supported_area
from app.providers import ProviderError, discover_places, place_details_many, resolve_origin, route


class OpenStreetMapProviderTests(unittest.TestCase):
    def test_photon_resolves_location_with_city_bias(self):
        async def run():
            with patch("app.providers._request_json", new=AsyncMock(return_value={"features": [
                {"properties": {"countrycode": "IN"}, "geometry": {"coordinates": [79.82, 11.92]}}
            ]})) as request:
                result = await resolve_origin("Puducherry Railway Station")
            return result, request.call_args.kwargs["params"]
        point, params = asyncio.run(run())
        self.assertEqual(point, {"lat": 11.92, "lng": 79.82})
        self.assertEqual(params["countrycode"], "IN")
        self.assertEqual(params["lat"], supported_area()["center"]["lat"])

    def test_explicit_coordinates_do_not_call_geocoder(self):
        async def run():
            with patch("app.providers._request_json", new=AsyncMock()) as request:
                point = await resolve_origin("12.9716, 77.5946")
            return point, request
        point, request = asyncio.run(run())
        self.assertEqual(point, {"lat": 12.9716, "lng": 77.5946})
        request.assert_not_awaited()

    def test_osrm_route_returns_real_geometry_and_measurements(self):
        async def run():
            payload = {"code": "Ok", "routes": [{"duration": 123.2, "distance": 900.5, "geometry": {"type": "LineString", "coordinates": [[79.8, 11.9], [79.81, 11.91]]}}]}
            with patch("app.providers._request_json", new=AsyncMock(return_value=payload)) as request:
                result = await route({"lat": 11.9, "lng": 79.8}, {"lat": 11.91, "lng": 79.81}, "WALK")
            return result, request
        result, request = asyncio.run(run())
        self.assertEqual(result["distance_meters"], 900)
        self.assertEqual(result["duration"], "124s")
        self.assertEqual(result["geometry"]["type"], "LineString")
        self.assertIn("routed-foot", request.await_args.args[1])

    def test_transit_route_is_not_silently_substituted(self):
        async def run():
            await route({"lat": 11.9, "lng": 79.8}, {"lat": 11.91, "lng": 79.81}, "TRANSIT")
        with self.assertRaisesRegex(ProviderError, "does not provide transit"):
            asyncio.run(run())

    def test_overpass_saved_refresh_uses_osm_ids_and_returns_only_matched_objects(self):
        async def run():
            payload = {"elements": [
                {"type": "node", "id": 123, "lat": 11.916064, "lon": 79.812325, "tags": {"name": "Mapped Cafe", "amenity": "cafe", "diet:vegetarian": "yes"}},
                {"type": "node", "id": 456, "lat": 10.925, "lon": 79.838, "tags": {"name": "Outside", "amenity": "cafe"}},
            ]}
            with patch("app.providers._request_json", new=AsyncMock(return_value=payload)) as request:
                result = await place_details_many(["osm:node:123", "osm:node:999", "google-id"])
            return result, request
        result, request = asyncio.run(run())
        self.assertEqual(list(result), ["osm:node:123"])
        self.assertTrue(result["osm:node:123"]["dietary_options"]["vegetarian"])
        self.assertIn("node(id:123,999)", request.await_args.kwargs["data"]["data"])

    def test_discovery_filters_missing_and_out_of_scope_coordinates(self):
        async def run():
            payload = {"elements": [
                {"type": "node", "id": 1, "lat": 11.916064, "lon": 79.812325, "tags": {"name": "In-scope", "amenity": "cafe"}},
                {"type": "node", "id": 2, "lat": 10.925, "lon": 79.838, "tags": {"name": "Distant", "amenity": "cafe"}},
                {"type": "way", "id": 3, "tags": {"name": "No center", "amenity": "cafe"}},
            ]}
            with patch("app.providers._request_json", new=AsyncMock(return_value=payload)):
                return await discover_places("Pondicherry", "cafe", 12)
        found = asyncio.run(run())
        self.assertEqual([place["id"] for place in found], ["osm:node:1"])


if __name__ == "__main__":
    unittest.main()

