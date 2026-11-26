"""Tool 4: getTrafficReport.

Google Routes (traffic-aware, needs GOOGLE_MAPS_API_KEY) when possible; otherwise a
distance-based estimate that is labelled as an estimate and never as live traffic.
"""
import datetime as dt
import logging
import math
from typing import Optional

from ..config import get_assumptions
from ..schemas.context import Point, RouteEstimate
from ..services import routing
from ..services.geo import ProviderError
from ..utils import haversine_km

log = logging.getLogger(__name__)


def resolve_mode(mode: str, straight_km: float) -> str:
    """'flexible' becomes walking for short hops, taxi otherwise."""
    if mode != "flexible":
        return mode
    return "walking" if straight_km <= get_assumptions()["walk_max_km_when_flexible"] else "taxi"


def _congestion(mode: str, depart: dt.datetime) -> float:
    if mode == "walking":
        return 1.0
    a = get_assumptions()
    factor = next((c["factor"] for c in a["congestion"] if c["from"] <= depart.hour < c["to"]), 1.0)
    if depart.weekday() >= 5 and factor > 1:
        factor = 1 + (factor - 1) * a["weekend_congestion_scale"]
    return factor


def estimate_route(origin: Point, dest: Point, depart: dt.datetime, mode: str) -> RouteEstimate:
    a = get_assumptions()
    straight = haversine_km(origin.lat, origin.lon, dest.lat, dest.lon)
    m = resolve_mode(mode, straight)
    dist = straight * a["road_factor"]
    factor = _congestion(m, depart)
    base = dist / a["speeds_kmh"][m] * 60
    duration = max(base * factor, 3.0) if dist > 0.05 else 0.0
    cond = "heavy" if factor >= 1.5 else "moderate" if factor >= 1.2 else "light"
    return RouteEstimate(
        origin=origin.name, destination=dest.name, distance_km=round(dist, 2),
        duration_min=round(duration, 1), delay_min=round(duration - base, 1) if duration else 0.0,
        traffic_condition=cond, departure_time=depart, mode=m, source="estimate", is_live_traffic=False,
        note="Estimated from distance and typical congestion for that time of day; not live traffic.",
    )


def get_traffic_report(
    origin: Point, dest: Point, depart: dt.datetime, mode: str, now: Optional[dt.datetime] = None
) -> RouteEstimate:
    straight = haversine_km(origin.lat, origin.lon, dest.lat, dest.lon)
    m = resolve_mode(mode, straight)
    est = estimate_route(origin, dest, depart, m)
    if not routing.available() or straight < 0.05:
        return est
    now = now or dt.datetime.now(dt.timezone.utc)
    future = depart > now + dt.timedelta(minutes=1)
    try:
        r = routing.compute_route((origin.lat, origin.lon), (dest.lat, dest.lon), m, depart if future else None)
    except ProviderError as e:
        log.warning("routing failed, using estimate: %s", e)
        est.note = "Live routing unavailable; " + (est.note or "").lower()
        return est
    delay = max(r["duration_min"] - r["static_min"], 0.0)
    ratio = r["duration_min"] / r["static_min"] if r["static_min"] else 1.0
    cond = "heavy" if ratio >= 1.5 else "moderate" if ratio >= 1.2 else "light"
    return RouteEstimate(
        origin=origin.name, destination=dest.name, distance_km=round(r["distance_km"], 2),
        duration_min=round(math.ceil(r["duration_min"]), 1), delay_min=round(delay, 1),
        traffic_condition=cond if r["traffic_aware"] else "unknown", departure_time=depart, mode=m,
        source="google_routes", is_live_traffic=bool(r["traffic_aware"] and not future),
        note=("Traffic-aware prediction for the planned departure time." if r["traffic_aware"] and future
              else "Routing without traffic data (mode does not support it)." if not r["traffic_aware"]
              else "Current traffic."),
    )
