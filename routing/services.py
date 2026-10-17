import math
import os

import pandas as pd
import requests
from django.conf import settings
from shapely.geometry import LineString, Point
from shapely.ops import nearest_points

DATA_PATH = os.path.join(settings.BASE_DIR, "fuel_stations_with_coords.csv")

MAX_RANGE_MILES = 500.0
MPG = 10.0
TANK_CAPACITY_GALLONS = MAX_RANGE_MILES / MPG
PREFERRED_LEG_MIN_MILES = 380.0
PREFERRED_LEG_MAX_MILES = 460.0
FALLBACK_LEG_MIN_MILES = 300.0
MAX_STATION_DETOUR_MILES = 20.0
ORIGIN_PRICE_SEARCH_MILES = 50.0
MILES_PER_METER = 0.000621371


class RoutingServiceError(Exception):
    """The external routing service could not provide a usable route."""


class InfeasibleRouteError(Exception):
    """The route cannot satisfy the vehicle range with the available stations."""


def _load_stations():
    if not os.path.exists(DATA_PATH):
        raise RuntimeError(f"Fuel station data file is missing: {DATA_PATH}")

    frame = pd.read_csv(DATA_PATH)
    required = {
        "OPIS Truckstop ID",
        "Truckstop Name",
        "Address",
        "City",
        "State",
        "Retail Price",
        "lat",
        "lon",
    }
    missing = required.difference(frame.columns)
    if missing:
        raise RuntimeError(
            "Fuel station data is missing columns: " + ", ".join(sorted(missing))
        )

    numeric_columns = ["Retail Price", "lat", "lon"]
    for column in numeric_columns:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=numeric_columns)
    frame = frame[frame["Retail Price"] > 0].copy()
    return frame


STATIONS_DF = _load_stations()
FALLBACK_ORIGIN_PRICE = float(STATIONS_DF["Retail Price"].median())
HTTP_SESSION = requests.Session()
OSRM_BASE_URL = os.getenv("OSRM_BASE_URL", "http://router.project-osrm.org").rstrip(
    "/"
)


def _haversine_miles(lat1, lon1, lat2, lon2):
    earth_radius_miles = 3958.7613
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )
    return 2 * earth_radius_miles * math.asin(math.sqrt(a))


def _fetch_route(start_coords, finish_coords):
    osrm_url = (
        f"{OSRM_BASE_URL}/route/v1/driving/"
        f"{start_coords[1]},{start_coords[0]};"
        f"{finish_coords[1]},{finish_coords[0]}"
    )
    try:
        response = HTTP_SESSION.get(
            osrm_url,
            params={"overview": "full", "geometries": "geojson", "steps": "false"},
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise RoutingServiceError(
            "The routing provider is temporarily unavailable."
        ) from exc

    if payload.get("code") != "Ok" or not payload.get("routes"):
        raise RoutingServiceError(
            "No drivable route was found between the supplied locations."
        )

    route = payload["routes"][0]
    geometry = route.get("geometry", {})
    if geometry.get("type") != "LineString" or len(geometry.get("coordinates", [])) < 2:
        raise RoutingServiceError("The routing provider returned invalid geometry.")
    return route


def _stations_along_route(route_coords, total_distance_miles):
    route_line = LineString(route_coords)
    # Broad degree-based prefilter, followed by a distance-in-miles check.
    prefilter = route_line.buffer(0.55)
    candidates = []

    for row in STATIONS_DF.itertuples(index=False, name=None):
        station_id, name, address, city, state, _, price, latitude, longitude = row
        longitude = float(longitude)
        latitude = float(latitude)
        point = Point(longitude, latitude)
        if not prefilter.intersects(point):
            continue

        projected = nearest_points(route_line, point)[0]
        detour_miles = _haversine_miles(
            latitude, longitude, projected.y, projected.x
        )
        if detour_miles > MAX_STATION_DETOUR_MILES:
            continue

        progression = route_line.project(point, normalized=True) * total_distance_miles
        if progression <= 0.5 or progression >= total_distance_miles - 0.5:
            continue

        candidates.append(
            {
                "station_id": int(station_id),
                "name": str(name).strip(),
                "address": ", ".join(
                    part.strip()
                    for part in (str(address), str(city), str(state))
                    if part.strip()
                ),
                "price": float(price),
                "lat": latitude,
                "lon": longitude,
                "miles_along_route": progression,
                "detour_miles": detour_miles,
            }
        )

    # City-level source coordinates can place several stations at one point.
    # At an identical position only the cheapest station can be optimal.
    cheapest_by_position = {}
    for station in candidates:
        key = round(station["miles_along_route"], 2)
        existing = cheapest_by_position.get(key)
        if existing is None or station["price"] < existing["price"]:
            cheapest_by_position[key] = station

    return sorted(
        cheapest_by_position.values(), key=lambda item: item["miles_along_route"]
    )


def _origin_price(candidates, total_distance_miles):
    search_limit = min(ORIGIN_PRICE_SEARCH_MILES, total_distance_miles)
    nearby = [
        station["price"]
        for station in candidates
        if station["miles_along_route"] <= search_limit
    ]
    return min(nearby) if nearby else FALLBACK_ORIGIN_PRICE


def _optimize_fuel_purchases(candidates, total_distance_miles, origin_price):
    """Return a practical, price-aware plan for ordered route stations.

    Stops are normally selected 380-460 miles apart, leaving a 40-mile reserve
    below the vehicle's hard 500-mile range. The cheapest viable station in the
    window is selected. A wider window is used only when the preferred window
    contains no station.
    """
    current = {
        "miles_along_route": 0.0,
        "price": float(origin_price),
        "is_origin": True,
    }
    total_cost = 0.0
    total_purchased = 0.0
    origin_gallons = 0.0
    fuel_stops = []
    travel_legs = []
    fallback_windows_used = 0

    while current["miles_along_route"] < total_distance_miles - 1e-6:
        current_position = current["miles_along_route"]
        maximum_position = current_position + MAX_RANGE_MILES + 1e-6

        destination_reachable = total_distance_miles <= maximum_position
        if destination_reachable:
            target = {
                "miles_along_route": total_distance_miles,
                "is_destination": True,
            }
            window_used = "destination"
        else:
            preferred = [
                station
                for station in candidates
                if current_position + PREFERRED_LEG_MIN_MILES
                <= station["miles_along_route"]
                <= current_position + PREFERRED_LEG_MAX_MILES
            ]
            fallback = [
                station
                for station in candidates
                if current_position + FALLBACK_LEG_MIN_MILES
                <= station["miles_along_route"]
                <= maximum_position
            ]
            emergency = [
                station
                for station in candidates
                if current_position + 1e-6
                < station["miles_along_route"]
                <= maximum_position
            ]

            if preferred:
                selection_pool = preferred
                window_used = "preferred_380_460"
            elif fallback:
                selection_pool = fallback
                window_used = "fallback_300_500"
                fallback_windows_used += 1
            elif emergency:
                selection_pool = emergency
                window_used = "emergency_under_300"
                fallback_windows_used += 1
            else:
                raise InfeasibleRouteError(
                    "No fuel station in the supplied dataset is reachable within "
                    "the vehicle's 500-mile range."
                )

            # Avoid a cheap stop that leaves no possible station or destination
            # within the following 500 miles.
            viable = [
                station
                for station in selection_pool
                if total_distance_miles - station["miles_along_route"]
                <= MAX_RANGE_MILES
                or any(
                    station["miles_along_route"] + 1e-6
                    < onward["miles_along_route"]
                    <= station["miles_along_route"] + MAX_RANGE_MILES
                    for onward in candidates
                )
            ]
            if viable:
                selection_pool = viable

            target = min(
                selection_pool,
                key=lambda station: (
                    station["price"],
                    station["detour_miles"],
                    -station["miles_along_route"],
                ),
            )

        leg_distance = target["miles_along_route"] - current_position
        gallons_needed = leg_distance / MPG
        gallons_to_buy = gallons_needed
        purchase_cost = gallons_to_buy * current["price"]

        total_purchased += gallons_to_buy
        total_cost += purchase_cost

        if current.get("is_origin"):
            origin_gallons += gallons_to_buy
        elif gallons_to_buy > 1e-6:
            fuel_stops.append(
                {
                    "station_id": current["station_id"],
                    "station_name": current["name"],
                    "address": current["address"],
                    "price_per_gallon": round(current["price"], 3),
                    "gallons_purchased": round(gallons_to_buy, 2),
                    "purchase_cost_usd": round(purchase_cost, 2),
                    "distance_from_start_miles": round(current_position, 1),
                    "detour_from_route_miles": round(current["detour_miles"], 1),
                    "coordinates": [current["lat"], current["lon"]],
                }
            )

        travel_legs.append(
            {
                "from_mile": round(current_position, 1),
                "to_mile": round(target["miles_along_route"], 1),
                "distance_miles": round(leg_distance, 1),
                "selection_window": window_used,
            }
        )
        current = target

    return {
        "origin_fuel_price_per_gallon": round(float(origin_price), 3),
        "origin_gallons_purchased": round(origin_gallons, 2),
        "total_gallons_purchased": round(total_purchased, 2),
        "total_fuel_cost_usd": round(total_cost, 2),
        "fuel_stops": fuel_stops,
        "travel_legs": travel_legs,
        "fallback_windows_used": fallback_windows_used,
    }


def calculate_optimal_fuel_route(start_coords, finish_coords):
    route = _fetch_route(start_coords, finish_coords)
    total_distance_miles = float(route["distance"]) * MILES_PER_METER
    route_coords = route["geometry"]["coordinates"]
    candidates = _stations_along_route(route_coords, total_distance_miles)
    origin_price = _origin_price(candidates, total_distance_miles)
    plan = _optimize_fuel_purchases(candidates, total_distance_miles, origin_price)

    fuel_stops = plan["fuel_stops"]
    return {
        "total_distance_miles": round(total_distance_miles, 2),
        "total_gallons_consumed": round(total_distance_miles / MPG, 2),
        "total_gallons_purchased": plan["total_gallons_purchased"],
        "total_fuel_cost_usd": plan["total_fuel_cost_usd"],
        "origin_fueling": {
            "estimated_price_per_gallon": plan["origin_fuel_price_per_gallon"],
            "gallons_purchased": plan["origin_gallons_purchased"],
        },
        "fuel_stops_count": len(fuel_stops),
        "fuel_stops": fuel_stops,
        "travel_legs": plan["travel_legs"],
        "fallback_windows_used": plan["fallback_windows_used"],
        "route_geometry": route["geometry"],
        "assumptions": {
            "vehicle_mpg": MPG,
            "maximum_range_miles": MAX_RANGE_MILES,
            "tank_capacity_gallons": TANK_CAPACITY_GALLONS,
            "starts_with_empty_tank": True,
            "preferred_stop_window_miles": [
                PREFERRED_LEG_MIN_MILES,
                PREFERRED_LEG_MAX_MILES,
            ],
            "fallback_stop_window_miles": [
                FALLBACK_LEG_MIN_MILES,
                MAX_RANGE_MILES,
            ],
            "origin_price_method": (
                "Cheapest dataset station in the first 50 route miles; "
                "dataset median when none is available."
            ),
            "station_coordinates": (
                "Approximate city-level coordinates derived offline from the "
                "provided station data."
            ),
        },
    }
