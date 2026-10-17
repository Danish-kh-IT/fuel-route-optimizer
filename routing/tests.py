from unittest.mock import patch

from django.test import SimpleTestCase
from rest_framework.test import APIRequestFactory

from .services import InfeasibleRouteError, _optimize_fuel_purchases
from .views import RouteOptimizeView


def station(position, price, station_id=1):
    return {
        "station_id": station_id,
        "name": f"Station {station_id}",
        "address": "Test address",
        "price": price,
        "lat": 40.0,
        "lon": -100.0,
        "miles_along_route": float(position),
        "detour_miles": 1.0,
    }


class FuelOptimizationTests(SimpleTestCase):
    def test_short_route_uses_origin_price_instead_of_hard_coded_price(self):
        plan = _optimize_fuel_purchases([], 120.0, origin_price=3.0)

        self.assertEqual(plan["total_gallons_purchased"], 12.0)
        self.assertEqual(plan["total_fuel_cost_usd"], 36.0)
        self.assertEqual(plan["fuel_stops"], [])
        self.assertEqual(plan["travel_legs"][0]["distance_miles"], 120.0)

    def test_buys_fuel_for_each_planned_leg(self):
        candidates = [station(400, 3.0, 10), station(800, 5.0, 20)]

        plan = _optimize_fuel_purchases(candidates, 900.0, origin_price=4.0)

        self.assertEqual(plan["origin_gallons_purchased"], 40.0)
        self.assertEqual(plan["fuel_stops"][0]["station_id"], 10)
        self.assertEqual(plan["fuel_stops"][0]["gallons_purchased"], 50.0)
        self.assertEqual(plan["total_gallons_purchased"], 90.0)
        self.assertEqual(plan["total_fuel_cost_usd"], 310.0)

    def test_ignores_an_early_cheap_station_when_preferred_window_exists(self):
        candidates = [
            station(65, 1.0, 5),
            station(400, 3.0, 10),
            station(800, 3.1, 20),
        ]

        plan = _optimize_fuel_purchases(candidates, 900.0, origin_price=4.0)

        self.assertEqual(plan["fuel_stops"][0]["station_id"], 10)
        self.assertEqual(plan["travel_legs"][0]["distance_miles"], 400.0)
        self.assertEqual(
            plan["travel_legs"][0]["selection_window"], "preferred_380_460"
        )

    def test_uses_safe_fallback_when_preferred_window_has_no_station(self):
        candidates = [station(350, 3.0, 10), station(750, 3.1, 20)]

        plan = _optimize_fuel_purchases(candidates, 1100.0, origin_price=4.0)

        self.assertEqual(plan["travel_legs"][0]["distance_miles"], 350.0)
        self.assertEqual(
            plan["travel_legs"][0]["selection_window"], "fallback_300_500"
        )
        self.assertEqual(plan["fallback_windows_used"], 1)

    def test_every_generated_leg_respects_500_mile_range(self):
        candidates = [station(400, 4.0, 10), station(800, 4.0, 20)]

        plan = _optimize_fuel_purchases(candidates, 1200.0, origin_price=5.0)

        self.assertTrue(
            all(leg["distance_miles"] <= 500 for leg in plan["travel_legs"])
        )
        self.assertEqual(plan["total_gallons_purchased"], 120.0)

    def test_fails_when_no_station_is_reachable(self):
        with self.assertRaises(InfeasibleRouteError):
            _optimize_fuel_purchases([], 700.0, origin_price=3.0)


class RouteOptimizeViewTests(SimpleTestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.view = RouteOptimizeView.as_view()

    def post(self, payload):
        request = self.factory.post("/api/optimize-route/", payload, format="json")
        return self.view(request)

    def test_requires_both_locations(self):
        response = self.post({"start": "Dallas, TX"})

        self.assertEqual(response.status_code, 400)
        self.assertIn("finish", response.data)

    def test_rejects_identical_locations(self):
        response = self.post(
            {"start": "Dallas, TX", "finish": "dallas, tx"}
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("non_field_errors", response.data)

    @patch("routing.views.calculate_optimal_fuel_route")
    @patch("routing.views._geocode_us")
    def test_returns_optimized_route(self, geocode, optimize):
        geocode.side_effect = [(32.7767, -96.7970), (34.0522, -118.2437)]
        optimize.return_value = {
            "total_distance_miles": 1435.0,
            "fuel_stops": [],
        }

        response = self.post(
            {"start": "Dallas, TX", "finish": "Los Angeles, CA"}
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["total_distance_miles"], 1435.0)
        optimize.assert_called_once_with(
            (32.7767, -96.7970), (34.0522, -118.2437)
        )

    @patch("routing.views._geocode_us", return_value=None)
    def test_rejects_unresolved_location(self, _geocode):
        response = self.post(
            {"start": "Not a place", "finish": "Los Angeles, CA"}
        )

        self.assertEqual(response.status_code, 400)

    @patch("routing.views.calculate_optimal_fuel_route")
    @patch("routing.views._geocode_us")
    def test_returns_422_for_an_infeasible_500_mile_plan(self, geocode, optimize):
        geocode.side_effect = [(32.7767, -96.7970), (34.0522, -118.2437)]
        optimize.side_effect = InfeasibleRouteError("No reachable station")

        response = self.post(
            {"start": "Dallas, TX", "finish": "Los Angeles, CA"}
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.data["error"], "No reachable station")
