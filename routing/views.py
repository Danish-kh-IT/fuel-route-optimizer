import logging
import os
from functools import lru_cache

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderServiceError
from geopy.extra.rate_limiter import RateLimiter

from .serializers import RouteRequestSerializer
from .services import (
    InfeasibleRouteError,
    RoutingServiceError,
    calculate_optimal_fuel_route,
)

logger = logging.getLogger(__name__)

geolocator = Nominatim(
    user_agent=os.getenv(
        "NOMINATIM_USER_AGENT", "fuel-route-optimizer-assessment/1.0"
    ),
    scheme="https",
)
rate_limited_geocode = RateLimiter(
    geolocator.geocode,
    min_delay_seconds=1.0,
    swallow_exceptions=False,
)


@lru_cache(maxsize=512)
def _geocode_us(location_name):
    location = rate_limited_geocode(
        f"{location_name}, USA",
        country_codes="us",
        addressdetails=True,
        exactly_one=True,
        timeout=10,
    )
    if not location:
        return None

    country_code = location.raw.get("address", {}).get("country_code", "").lower()
    if country_code != "us":
        return None

    return float(location.latitude), float(location.longitude)


class RouteOptimizeView(APIView):
    def post(self, request):
        serializer = RouteRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        start = serializer.validated_data["start"]
        finish = serializer.validated_data["finish"]

        try:
            start_coords = _geocode_us(start)
            finish_coords = _geocode_us(finish)

            if not start_coords or not finish_coords:
                return Response(
                    {
                        "error": (
                            "Unable to resolve both locations inside the USA. "
                            "Use a city and two-letter state code, for example 'Dallas, TX'."
                        )
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            data = calculate_optimal_fuel_route(start_coords, finish_coords)
            return Response(data, status=status.HTTP_200_OK)
        except GeocoderServiceError:
            logger.exception("Geocoding provider failed")
            return Response(
                {"error": "The geocoding provider is temporarily unavailable."},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        except InfeasibleRouteError as exc:
            return Response(
                {"error": str(exc)}, status=status.HTTP_422_UNPROCESSABLE_ENTITY
            )
        except RoutingServiceError as exc:
            logger.warning("Routing provider failed: %s", exc)
            return Response(
                {"error": str(exc)}, status=status.HTTP_502_BAD_GATEWAY
            )
        except Exception:
            logger.exception("Unexpected route optimization failure")
            return Response(
                {"error": "Unable to optimize the route due to an internal error."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
