# Optimal Fuel Route Planning API

A robust RESTful API built with **Django REST Framework (DRF)** that determines
a practical, cost-effective driving route between two locations in the USA.
It dynamically selects refueling stations using fuel prices, vehicle range,
route position, and fuel efficiency.

---

## Technical Specifications & Assumptions

- **Framework**: Django 6.1.2 and Django REST Framework.
- **Vehicle Range**: Maximum 500 miles on a full 50-gallon tank.
- **Fuel Efficiency**: 10 miles per gallon (MPG).
- **Location Validation**: Start and finish must resolve to locations in the
  USA.
- **Geocoding**: Nominatim resolves both input locations. Results are cached
  in-process and public-service rate limits are respected.
- **Routing Engine**: Open Source Routing Machine (OSRM) public API. The API
  makes one routing call per calculation; an uncached request makes up to three
  external calls including the two geocoding lookups.
- **Fuel Data Processing**: The provided station-price dataset is enriched
  offline with approximate city-level coordinates and loaded into memory when
  Django starts.
- **Detour Corridor**: Shapely filters stations to an estimated maximum
  20-mile perpendicular distance from the route.
- **Preferred Stop Window**: The cheapest viable station between 380 and 460
  miles from the previous stop is selected, leaving a safety reserve below the
  500-mile limit.
- **Fallback Window**: A 300-500 mile window is used only when the preferred
  window contains no viable station. Any fallback is reported in the response.
- **Starting Fuel**: The vehicle starts with an empty tank and purchases the
  fuel required for each planned leg.
- **Origin Price**: Estimated from the cheapest candidate station in the first
  50 route miles, or the dataset median when no nearby candidate exists.

---

## Project Structure

```text
fuel-route-optimizer/
|
|-- core/                              # Django configuration
|   |-- settings.py                    # Environment-aware settings
|   |-- urls.py                        # Root URL configuration
|   |-- asgi.py
|   `-- wsgi.py
|
|-- routing/                           # Route and fuel optimization app
|   |-- serializers.py                 # Request validation
|   |-- services.py                    # Spatial search and optimization
|   |-- views.py                       # REST API handlers
|   |-- urls.py                        # API routes
|   |-- tests.py                       # Automated tests
|   `-- migrations/
|
|-- preprocess.py                      # Offline coordinate preprocessing
|-- fuel_stations_with_coords.csv      # Coordinate-enriched station data
|-- fuel-prices-for-be-assessment.csv  # Original supplied fuel data
|-- requirements.txt                   # Compatible dependency ranges
|-- .env.example                       # Environment variable examples
|-- .python-version                    # Python version
|-- .gitignore
|-- manage.py
`-- README.md
```

---

## Installation & Setup

### 1. Clone and create a virtual environment

```bash
git clone (https://github.com/Danish-kh-IT/fuel-route-optimizer)
cd fuel-route-optimizer
python -m venv .venv
```

Activate it in Git Bash:

```bash
source .venv/Scripts/activate
```

Activate it in PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

Activate it on Linux or macOS:

```bash
source .venv/bin/activate
```

### 2. Install dependencies

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### 3. Data preprocessing (optional)

The processed station file is included. If
`fuel_stations_with_coords.csv` is missing, regenerate it from the supplied raw
dataset:

```bash
python preprocess.py
```

### 4. Run migrations and start the server

```bash
python manage.py migrate
python manage.py runserver
```

The development server starts at `http://127.0.0.1:8000/`.

---

## Environment Configuration

Local defaults work without an `.env` loader. For deployment, configure the
values shown in `.env.example` through the hosting environment:

```text
DJANGO_SECRET_KEY=replace-with-a-long-random-secret
DJANGO_DEBUG=false
DJANGO_ALLOWED_HOSTS=api.example.com
NOMINATIM_USER_AGENT=fuel-route-optimizer-assessment/1.0
OSRM_BASE_URL=http://router.project-osrm.org
```

---

## API Documentation

### Optimize route and fuel stops

- **Endpoint**: `POST /api/optimize-route/`
- **Content-Type**: `application/json`

#### Request payload

```json
{
  "start": "New York, NY",
  "finish": "Los Angeles, CA"
}
```

#### Example request

```bash
curl -X POST http://127.0.0.1:8000/api/optimize-route/ \
  -H "Content-Type: application/json" \
  -d '{"start":"New York, NY","finish":"Los Angeles, CA"}'
```

#### Successful response (`200 OK`)

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
      "station_id": 72445,
      "station_name": "SHEETZ #639",
      "address": "I-80 Exit 223, Youngstown, OH",
      "price_per_gallon": 3.059,
      "gallons_purchased": 39.92,
      "purchase_cost_usd": 122.11,
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
    "coordinates": [
      [-74.006, 40.7128],
      [-74.010, 40.7135]
    ]
  },
  "assumptions": {
    "vehicle_mpg": 10.0,
    "maximum_range_miles": 500.0,
    "preferred_stop_window_miles": [380.0, 460.0]
  }
}
```

The example is shortened. The real response contains all stops, travel legs,
assumptions, and the complete GeoJSON route geometry.

---

## Architecture & Algorithm Highlights

1. **Validated USA Inputs**: A DRF serializer validates the payload before
   Nominatim resolves both locations with a US-only filter.
2. **Single Routing Call**: OSRM returns the complete driving distance and
   GeoJSON route in one routing request.
3. **Local Station Search**: More than 7,000 coordinate-enriched fuel records
   are searched locally, avoiding station lookup calls during optimization.
4. **Linear Route Projection**: Shapely's `project(normalized=True)` determines
   each candidate station's progress along the route.
5. **Dynamic Stop Window**: The optimizer rejects premature cheap stops and
   selects the lowest-priced viable station in the preferred 380-460 mile
   window.
6. **Forward Reachability**: A station is preferred only when another station
   or the destination remains reachable within 500 miles.
7. **Transparent Fallbacks**: Wider stop windows are used only when required
   and are exposed through `fallback_windows_used` and `travel_legs`.
8. **Explicit Failure**: The API returns `422 Unprocessable Entity` when the
   available station data cannot produce a valid 500-mile-range plan.

---

## Error Responses

| Status | Meaning |
| --- | --- |
| `400 Bad Request` | Missing, identical, invalid, or non-USA locations |
| `422 Unprocessable Entity` | No station plan satisfies the 500-mile range |
| `502 Bad Gateway` | Geocoding or routing provider is unavailable |
| `500 Internal Server Error` | Unexpected internal failure |

---

## Testing

```bash
python manage.py test
```

The 11 automated tests cover request validation, short-route calculations,
preferred and fallback stop windows, rejection of unnecessary early stops,
hard 500-mile range enforcement, successful API output, and infeasible routes.

---

## Performance

Station data is loaded once, geocoding results are cached, and only one routing
call is made per calculation. During local verification, a New York-to-Los
Angeles request returned six realistic stops in approximately five seconds.
Actual response time depends on the public geocoding and routing services.

---

## Known Data Limitation

The assessment CSV contains station addresses and prices but no exact latitude
or longitude fields. The included preprocessing step therefore uses city-level
matching. This is suitable for demonstrating the route optimization design,
but a production system should replace it with verified station coordinates.

---
