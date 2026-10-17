# Fuel Route Optimizer API

A Django REST API that returns a US driving route, cost-aware fuel stops, and
the estimated fuel cost for a vehicle that gets 10 MPG and has a 500-mile
maximum range.

## Highlights

- Uses Nominatim to resolve the two US locations (with caching and its public
  rate limit respected) and OSRM for the route.
- Makes one routing request per uncached API request (three external requests
  including the two geocoding lookups).
- Loads and cleans the supplied station price data once when the process starts.
- Uses a practical dynamic window: normally select the cheapest viable station
  380-460 miles ahead, leaving a 40-mile reserve below maximum range.
- Falls back to a 300-500 mile window only when station coverage requires it;
  the response reports every fallback.
- Explicitly rejects a route when the supplied station data cannot support a
  leg of 500 miles or less.
- Returns GeoJSON route geometry, stop markers, purchase amounts, cost, and the
  individual travel legs.

## Requirements

- Python 3.12+
- Django 6.1.2

## Setup

```bash
python -m venv .venv
```

Activate the environment and install dependencies:

```bash
python -m pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

For local development the provided defaults work without an `.env` loader. In
deployment, set the variables shown in `.env.example` through the host's
environment. In particular, use a private `DJANGO_SECRET_KEY`, set
`DJANGO_DEBUG=false`, and configure `DJANGO_ALLOWED_HOSTS`.
`OSRM_BASE_URL` is configurable so a hosted OSRM instance can be substituted
for the public demonstration server without changing application code.

## Endpoint

`POST /api/optimize-route/`

Request:

```json
{
  "start": "Los Angeles, CA",
  "finish": "New York, NY"
}
```

Example with curl:

```bash
curl -X POST http://127.0.0.1:8000/api/optimize-route/ \
  -H "Content-Type: application/json" \
  -d '{"start":"Los Angeles, CA","finish":"New York, NY"}'
```

Important response fields:

- `route_geometry`: GeoJSON `LineString` for rendering the route on a map.
- `fuel_stops`: station coordinates, prices, gallons purchased, and cost.
- `travel_legs`: consecutive legs; every leg is at most 500 miles.
- `fallback_windows_used`: number of legs that could not use the preferred
  380-460 mile stop window.
- `total_gallons_consumed`: route distance divided by 10 MPG.
- `total_fuel_cost_usd`: cost of the optimized purchases, including origin fuel.
- `assumptions`: the calculation assumptions returned with every result.

## Fuel-cost assumptions

The vehicle starts with an empty 50-gallon tank. Because the supplied CSV does
not provide a price at an arbitrary origin, the origin price is estimated using
the cheapest candidate station in the first 50 route miles. The dataset median
is used if no such candidate exists. The API exposes this estimate in
`origin_fueling` instead of silently using a hard-coded price.

The supplied station file has no latitude/longitude fields. The included
preprocessed file therefore uses offline city-level coordinates. Stops are
limited to an estimated 20-mile corridor and the API reports each stop's
estimated route detour. For production use, exact station coordinates should
replace these city-level coordinates.

## Tests

```bash
python manage.py test
```

The tests cover validation, short-route cost, preferred and fallback stop
windows, rejection of early unnecessary stops, 500-mile leg enforcement,
successful API output, and infeasible routes.

## Loom demo outline

1. Send a valid long-distance request in Postman and show route geometry,
   multiple fuel stops, purchase costs, and `travel_legs`.
2. Show a validation error with a missing location.
3. Briefly explain `routing/views.py`, `routing/services.py`, and the tests.
4. Run `python manage.py test` and keep the full video under five minutes.
