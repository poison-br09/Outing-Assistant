"""External data providers: Open-Meteo (geocoding, forecast) and Geoapify (places, geocoding).

Error text deliberately never includes URLs: they can carry API keys.
"""
import datetime as dt
import logging
from typing import Optional

import httpx

from ..config import get_settings
from ..schemas.context import Location

log = logging.getLogger(__name__)


class ProviderError(Exception):
    def __init__(self, provider: str, message: str):
        super().__init__(f"{provider}: {message}")
        self.provider = provider
        self.message = message


def _get(provider: str, url: str, params: dict) -> dict:
    try:
        r = httpx.get(url, params=params, timeout=get_settings().http_timeout_seconds)
        r.raise_for_status()
        return r.json()
    except httpx.HTTPStatusError as e:
        raise ProviderError(provider, f"HTTP {e.response.status_code}") from None
    except (httpx.HTTPError, ValueError) as e:
        raise ProviderError(provider, type(e).__name__) from None


def _geoapify_key() -> str:
    key = get_settings().geoapify_api_key
    if not key:
        raise ProviderError("geoapify", "API key not configured")
    return key


# ---- Open-Meteo (no key) -------------------------------------------------

def resolve_city(name: str) -> Optional[Location]:
    """City -> coordinates + IANA timezone. None when the city is not found."""
    data = _get(
        "open-meteo-geocoding",
        "https://geocoding-api.open-meteo.com/v1/search",
        {"name": name, "count": 1, "language": "en", "format": "json"},
    )
    results = data.get("results") or []
    if not results:
        return None
    r = results[0]
    return Location(
        name=r["name"], lat=r["latitude"], lon=r["longitude"],
        timezone=r.get("timezone", "UTC"), country=r.get("country"),
    )


def fetch_forecast(lat: float, lon: float, date: dt.date, timezone: str) -> dict:
    return _get(
        "open-meteo",
        "https://api.open-meteo.com/v1/forecast",
        {
            "latitude": lat, "longitude": lon, "timezone": timezone,
            "start_date": date.isoformat(), "end_date": (date + dt.timedelta(days=1)).isoformat(),
            "hourly": "temperature_2m,precipitation_probability,precipitation,weathercode,windspeed_10m",
        },
    )


# ---- Geoapify (key) ------------------------------------------------------

def geocode_place(text: str, near: Optional[Location] = None) -> Optional[tuple[float, float]]:
    s = get_settings()
    params = {"text": text, "limit": 1, "apiKey": _geoapify_key()}
    if near:
        params["bias"] = f"proximity:{near.lon},{near.lat}"
    data = _get("geoapify", f"{s.geoapify_base_url}/v1/geocode/search", params)
    feats = data.get("features") or []
    if not feats:
        return None
    p = feats[0]["properties"]
    return p["lat"], p["lon"]


def search_places(
    categories: list[str], lat: float, lon: float, radius_m: int, limit: int,
    conditions: Optional[list[str]] = None,
) -> list[dict]:
    s = get_settings()
    params = {
        "categories": ",".join(categories),
        "filter": f"circle:{lon},{lat},{radius_m}",
        "bias": f"proximity:{lon},{lat}",
        "limit": limit,
        "apiKey": _geoapify_key(),
    }
    if conditions:
        params["conditions"] = ",".join(conditions)
    data = _get("geoapify", f"{s.geoapify_base_url}/v2/places", params)
    return [f["properties"] for f in data.get("features", [])]


def fetch_place_details(place_id: str) -> dict:
    """Geoapify Place Details: used to fill in opening hours/website the place search omitted."""
    s = get_settings()
    data = _get(
        "geoapify", f"{s.geoapify_base_url}/v2/place-details",
        {"id": place_id, "features": "details", "apiKey": _geoapify_key()},
    )
    merged: dict = {}
    for f in data.get("features", []):
        merged.update({k: v for k, v in (f.get("properties") or {}).items() if v})
    return merged
