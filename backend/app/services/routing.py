"""Google Maps Routes API (traffic-aware). Optional: without a key callers use estimates."""
import datetime as dt
from typing import Optional

import logging

import httpx

from ..config import get_settings
from .geo import ProviderError

log = logging.getLogger(__name__)

_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
_MODES = {"walking": "WALK", "public_transport": "TRANSIT", "two_wheeler": "TWO_WHEELER", "car": "DRIVE", "taxi": "DRIVE"}


def available() -> bool:
    return bool(get_settings().google_maps_api_key)


def compute_route(
    origin: tuple[float, float], dest: tuple[float, float], mode: str, depart: Optional[dt.datetime]
) -> dict:
    """Returns {distance_km, duration_min, static_min, traffic_aware}."""
    s = get_settings()
    travel_mode = _MODES[mode]
    body: dict = {
        "origin": {"location": {"latLng": {"latitude": origin[0], "longitude": origin[1]}}},
        "destination": {"location": {"latLng": {"latitude": dest[0], "longitude": dest[1]}}},
        "travelMode": travel_mode,
    }
    traffic = travel_mode in ("DRIVE", "TWO_WHEELER")
    if traffic:
        body["routingPreference"] = "TRAFFIC_AWARE"
    if depart is not None and travel_mode != "WALK":
        body["departureTime"] = depart.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    headers = {
        "X-Goog-Api-Key": s.google_maps_api_key,
        "X-Goog-FieldMask": "routes.duration,routes.staticDuration,routes.distanceMeters",
    }
    try:
        r = httpx.post(_URL, json=body, headers=headers, timeout=s.http_timeout_seconds)
        r.raise_for_status()
        route = r.json()["routes"][0]
        log.info("PROVIDER google-routes POST body=%s -> %s %s", body, r.status_code, route)
    except httpx.HTTPStatusError as e:
        raise ProviderError("google-routes", f"HTTP {e.response.status_code}") from None
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as e:
        raise ProviderError("google-routes", type(e).__name__) from None
    secs = lambda v: float(str(v).rstrip("s"))  # noqa: E731  e.g. "1234s"
    dur = secs(route["duration"]) / 60
    return {
        "distance_km": route.get("distanceMeters", 0) / 1000,
        "duration_min": dur,
        "static_min": secs(route.get("staticDuration", route["duration"])) / 60,
        "traffic_aware": traffic,
    }
