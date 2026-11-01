import os
import unittest
from unittest.mock import patch

from app.geography import location_in_supported_area, supported_area


class GeographyTests(unittest.TestCase):
    def test_reference_center_is_supported(self):
        center = supported_area()["center"]
        self.assertTrue(location_in_supported_area(center))

    def test_distant_union_territory_regions_are_out_of_scope(self):
        self.assertFalse(location_in_supported_area({"lat": 10.925, "lng": 79.838}))  # Karaikal
        self.assertFalse(location_in_supported_area({"lat": 11.7, "lng": 75.55}))  # Mahe
        self.assertFalse(location_in_supported_area({"lat": 16.73, "lng": 82.22}))  # Yanam

    def test_radius_is_configurable_and_enforced(self):
        with patch.dict(os.environ, {"PONDICHERRY_SEARCH_RADIUS_METERS": "1000"}):
            self.assertEqual(supported_area()["radius_meters"], 1000)
            self.assertFalse(location_in_supported_area({"lat": 11.95, "lng": 79.812}))
        with patch.dict(os.environ, {"PONDICHERRY_SEARCH_RADIUS_METERS": "100000"}):
            with self.assertRaises(ValueError):
                supported_area()

    def test_missing_invalid_and_uncertain_locations_are_rejected(self):
        for location in (None, {}, {"lat": None, "lng": None}, {"lat": 95, "lng": 79}):
            self.assertFalse(location_in_supported_area(location))


if __name__ == "__main__":
    unittest.main()
