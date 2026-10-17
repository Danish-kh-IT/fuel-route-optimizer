# Optimal Fuel Route Planning API

A RESTful API built with **Django REST Framework** that calculates a driving
route between two locations in the USA and recommends practical,
cost-effective fuel stops along that route. The response includes GeoJSON route
geometry, fuel-stop details, individual travel legs, total fuel consumption,
and estimated trip fuel cost.

---

## Technical Specifications & Assumptions

- **Framework**: Django 6.1.2 with Django REST Framework.
- **Supported Locations**: Start and finish must resolve to locations within
  the USA.
- **Vehicle Range**: Maximum 500 miles on a full 50-gallon tank.
- **Fuel Efficiency**: Fixed at 10 miles per gallon (MPG).
- **Geocoding**: Nominatim resolves the two input locations. Lookups are
  rate-limited and cached in the application process.
- **Routing Engine**: Open Source Routing Machine (OSRM) public API. Each
  calculation makes one routing call; an uncached request makes up to three
  external calls including the two geocoding lookups.
- **Route Output**: Full GeoJSON `LineString`, suitable for rendering with
  Leaflet, Mapbox, or another map client.
- **Fuel Data Processing**: The supplied fuel-price CSV is preprocessed once
  into a coordinate-enriched file and loaded into memory when Django starts.
- **Station Coordinates**: The source file does not contain coordinates, so
  the included preprocessing step maps stations to approximate city-level
  coordinates.
- **Detour Corridor**: Stations are filtered to an estimated maximum 20-mile
  perpendicular distance from the route.
- **Preferred Stop Window**: The cheapest viable station between 380 and 460
  miles from the previous stop is selected. This leaves at least a 40-mile
  reserve below maximum range.
- **Fallback Stop Window**: A 300-500 mile window is used only when the
  preferred window has no station. Emergency shorter stops are allowed only
  when required by dataset coverage, and every fallback is reported.
- **Origin Fuel Price**: Estimated using the cheapest route candidate in the
  first 50 miles. The dataset median is used when no nearby candidate exists.
- **Starting Fuel**: The vehicle starts with an empty tank and purchases only
  the fuel required for each planned leg.

---

## Project Structure

```text
fuel-route-optimizer/
|
|-- core/                              # Django project configuration
|   |-- settings.py                    # Environment-aware settings
|   |-- urls.py                        # Root URL configuration
|   |-- asgi.py
|   `-- wsgi.py
|
|-- routing/                           # Route and fuel optimization app
|   |-- serializers.py                 # Request validation
|   |-- services.py                    # Routing, spatial search, optimization
|   |-- views.py                       # REST API endpoint and error handling
|   |-- urls.py                        # API route definition
|   |-- tests.py                       # Automated test suite
|   `-- migrations/
|
|-- fuel-prices-for-be-assessment.csv  # Original supplied fuel-price data
|-- fuel_stations_with_coords.csv      # Preprocessed station data
|-- preprocess.py                      # Offline coordinate preprocessing
|-- requirements.txt                   # Compatible Python dependency ranges
|-- .env.example                       # Environment variable examples
|-- .python-version                    # Required Python version
|-- .gitignore
|-- manage.py
`-- README.md
```

---

## Optimization Flow

1. Validate the request and geocode both locations inside the USA.
2. Make one OSRM request for the complete driving route.
3. Build a route corridor and find candidate fuel stations near the route.
4. Starting at mile zero, search the preferred 380-460 mile window.
5. Select the cheapest viable station in that window, using detour and forward
   reachability as tie-break considerations.
6. Use the documented fallback window only when necessary.
7. Calculate gallons and purchase cost for every leg at 10 MPG.
8. Return fuel stops, travel legs, totals, assumptions, and GeoJSON geometry.

Every generated leg is validated against the hard 500-mile vehicle range. If
the supplied station data cannot support the route, the API returns an explicit
`422 Unprocessable Entity` response instead of an invalid plan.

---

## Installation

### Requirements

- Python 3.12+
- pip

Create the environment:

```bash
python -m venv .venv
```

Activate it in Git Bash:

```bash
source .venv/Scripts/activate
```

Or activate it in PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

Install and start the project:

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

The development API is available at `http://127.0.0.1:8000/`.

---

## Environment Configuration

The application has safe local-development defaults. For deployment, configure
the variables shown in `.env.example` through the hosting environment:

```text
DJANGO_SECRET_KEY=replace-with-a-long-random-secret
DJANGO_DEBUG=false
DJANGO_ALLOWED_HOSTS=api.example.com
NOMINATIM_USER_AGENT=fuel-route-optimizer-assessment/1.0
OSRM_BASE_URL=http://router.project-osrm.org
```

`OSRM_BASE_URL` is configurable so the public demonstration server can be
replaced with a dedicated OSRM instance without changing application code.

---

## API Endpoint

### Optimize a route

```http
POST /api/optimize-route/
Content-Type: application/json
```

Request body:

```json
{
  "start": "New York, NY",
  "finish": "Los Angeles, CA"
}
```

Example request:

```bash
curl -X POST http://127.0.0.1:8000/api/optimize-route/ \
  -H "Content-Type: application/json" \
  -d '{"start":"New York, NY","finish":"Los Angeles, CA"}'
```

Response summary:

```json
{
  "total_distance_miles": 2794.02,
  "total_gallons_consumed": 279.4,
  "total_gallons_purchased": 279.4,
  "total_fuel_cost_usd": 878.93,
  "origin_fueling": {
    "estimated_price_per_gallon": 3.099,
    "gallons_purchased": 40.05
  },
  "fuel_stops_count": 6,
  "fuel_stops": [
    {
      "station_name": "SHEETZ #639",
      "price_per_gallon": 3.059,
      "gallons_purchased": 39.92,
      "distance_from_start_miles": 400.5,
      "detour_from_route_miles": 3.8,
      "coordinates": [41.0986, -80.6474]
    }
  ],
  "fallback_windows_used": 0,
  "travel_legs": [
    {
      "from_mile": 0.0,
      "to_mile": 400.5,
      "distance_miles": 400.5,
      "selection_window": "preferred_380_460"
    }
  ],
  "route_geometry": {
    "type": "LineString",
    "coordinates": []
  },
  "assumptions": {}
}
```

The example is shortened; the real response contains every stop, travel leg,
assumption, and the complete route geometry.

### Important response fields

| Field | Description |
| --- | --- |
| `route_geometry` | Complete GeoJSON route for map rendering |
| `fuel_stops` | Selected stations, prices, purchases, detours, and coordinates |
| `travel_legs` | Consecutive journey legs and the selection window used |
| `fallback_windows_used` | Number of times the preferred window was unavailable |
| `total_gallons_consumed` | Route distance divided by 10 MPG |
| `total_fuel_cost_usd` | Total cost of origin and en-route fuel purchases |
| `assumptions` | Calculation rules returned transparently by the API |

---

## Error Responses

| Status | Meaning |
| --- | --- |
| `400 Bad Request` | Missing, identical, invalid, or non-USA locations |
| `422 Unprocessable Entity` | No valid station plan satisfies the 500-mile range |
| `502 Bad Gateway` | Geocoding or routing provider is unavailable |
| `500 Internal Server Error` | Unexpected internal failure |

---

## Tests

Run the automated suite:

```bash
python manage.py test
```

The 11 tests cover:

- Request validation and USA location failures.
- Short-route fuel calculations.
- Preferred 380-460 mile stop selection.
- Rejection of unnecessary early cheap stops.
- Safe fallback-window behavior.
- Hard 500-mile leg enforcement.
- Infeasible route handling.
- Successful API responses.

---

## Performance

Station data is loaded once, geocoding results are cached, and only one routing
call is made per calculation. In a local verification, a New York-to-Los
Angeles request returned six realistic fuel stops in approximately five
seconds. Actual response time depends on the two public external services.

---

## Known Data Limitation

The supplied assessment CSV includes station addresses and prices but not exact
latitude/longitude values. The included coordinate file therefore uses
city-level matching. This is suitable for demonstrating the routing and
optimization design, but a production system should replace it with verified
station coordinates.

---

## Loom Demonstration Outline

1. Send a New York-to-Los Angeles request in Postman.
2. Show the six fuel stops, 380-460 mile legs, total cost, and GeoJSON route.
3. Show one validation error using a missing location.
4. Briefly explain `routing/views.py`, `routing/services.py`, and
   `routing/tests.py`.
5. Run `python manage.py test` and keep the complete recording under five
   minutes.
