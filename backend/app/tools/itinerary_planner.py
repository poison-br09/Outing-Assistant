"""Tool 11: buildItinerary (plus candidate selection per plan variant)."""
import datetime as dt
import statistics
from typing import Callable, Optional

from ..config import get_assumptions
from ..schemas.context import Point, RouteEstimate, TemporalContext
from ..schemas.itinerary import Itinerary, RankedVenue, Stop, Venue
from ..schemas.preferences import Preferences
from ..utils import category_lookup, haversine_km
from .crowd_estimator import estimate_crowd_level
from .venue_availability import get_venue_availability

RouteFn = Callable[[Point, Point, dt.datetime, str], RouteEstimate]


def venue_point(v: Venue) -> Point:
    return Point(name=v.name, lat=v.lat, lon=v.lon)


def stop_duration(v: Venue) -> int:
    a = get_assumptions()
    return int(category_lookup(a["durations_min"], v.categories, a["default_stop_duration_min"]))


def _deadline(start: dt.datetime, t: TemporalContext) -> dt.datetime:
    return min(t.latest_end, start + dt.timedelta(minutes=t.duration_minutes))


def select_for_variant(
    variant: str, acts: list[RankedVenue], foods: list[RankedVenue], prefs: Preferences,
    temporal: TemporalContext, radius_m: int,
) -> tuple[list[Venue], Optional[Venue], dict[str, str]]:
    """Pick activities (geographically clustered) and one meal for a plan variant."""
    a = get_assumptions()
    g, total = prefs.group_size, prefs.total_budget
    budget_pp = max(total / g, 1)
    pool = [r for r in acts if r.venue.est_cost_per_person == 0] if variant == "low_cost" else list(acts)
    include_food = temporal.duration_minutes >= a["min_duration_for_meal_min"] and bool(foods)
    meal = a["meal_duration_min"] if include_food else 0
    top = pool[:5]
    avg = statistics.mean(stop_duration(r.venue) for r in top) if top else a["default_stop_duration_min"]
    n = int((temporal.duration_minutes - meal) // (avg + a["assumed_leg_min"] + a["stop_buffer_min"]))
    n = max(1, min(n, a["max_activity_stops"]))

    def key(r: RankedVenue) -> float:
        pp = r.venue.est_cost_per_person
        if variant in ("budget_friendly", "low_cost"):
            return r.score - 0.5 * pp / budget_pp
        if variant == "experience_first":
            return r.score + 0.1 * min(pp / budget_pp, 1)
        return r.score

    chosen: list[RankedVenue] = []
    prev: Optional[Venue] = None
    remaining = list(pool)
    while remaining and len(chosen) < n:
        def adj(r: RankedVenue) -> float:
            d = 0 if prev is None else haversine_km(prev.lat, prev.lon, r.venue.lat, r.venue.lon) * 1000
            return key(r) - 0.25 * d / max(radius_m, 1)
        best = max(remaining, key=adj)
        chosen.append(best)
        remaining.remove(best)
        prev = best.venue

    food: Optional[Venue] = None
    if include_food:
        spent = sum(r.venue.est_cost_per_person for r in chosen) * g
        room = max(total - spent, 0) * 0.85
        affordable = [f for f in foods if f.venue.est_cost_per_person * g <= room]
        cheapest = min(foods, key=lambda f: f.venue.est_cost_per_person)
        if variant == "experience_first":
            top_f = foods[:5]
            top = max(top_f, key=lambda f: f.score + 0.2 * f.venue.est_cost_per_person / budget_pp)
            food = top.venue
        elif variant == "low_cost":
            food = min(affordable, key=lambda f: f.venue.est_cost_per_person).venue if affordable else None
        elif variant == "budget_friendly":
            food = min((affordable or [cheapest])[:5], key=lambda f: f.venue.est_cost_per_person).venue
        else:
            food = (affordable or [cheapest])[0].venue
    why = {r.venue.id: " ".join(r.reasons) for r in chosen}
    if food:
        why[food.id] = " ".join(next(f.reasons for f in foods if f.venue.id == food.id))
    return [r.venue for r in chosen], food, why


def arrange(acts: list[Venue], food: Optional[Venue], start: dt.datetime) -> list[Venue]:
    """Place the meal where it lands closest to a typical meal time (else mid-way)."""
    if food is None:
        return list(acts)
    a = get_assumptions()
    gap = a["assumed_leg_min"] + a["stop_buffer_min"]
    times, t = [], start
    for v in acts:
        times.append(t)
        t += dt.timedelta(minutes=stop_duration(v) + gap)
    times.append(t)
    targets = [start.replace(hour=int(m["target"]), minute=0) for m in a["meals"].values()]
    k, dist = len(acts) // 2, None
    for i, ti in enumerate(times):
        d = min(abs((ti - tg).total_seconds()) for tg in targets) / 60
        if dist is None or d < dist:
            k, dist = i, d
    if dist is not None and dist > 150:
        k = (len(acts) + 1) // 2  # no meal time nearby: mid-way
    return [*acts[:k], food, *acts[k:]]


def build_itinerary(
    order: list[Venue], start: dt.datetime, start_point: Point, temporal: TemporalContext,
    prefs: Preferences, route_fn: RouteFn, variant: str, why: Optional[dict[str, str]] = None,
) -> Itinerary:
    a = get_assumptions()
    why = why or {}
    deadline = _deadline(start, temporal)
    cursor, prev = start, start_point
    stops: list[Stop] = []
    notes: list[str] = []
    travel = 0.0
    for v in order:
        p = venue_point(v)
        route = route_fn(prev, p, cursor, prefs.travel_mode)
        arrive = cursor + dt.timedelta(minutes=route.duration_min)
        room = (deadline - arrive).total_seconds() / 60
        if room < a["min_stop_duration_min"]:
            notes.append(f"Dropped {v.name}: not enough time left in your window.")
            continue
        planned = stop_duration(v)
        dur = min(planned, int(room))
        if dur < planned:
            notes.append(f"Shortened {v.name} to {dur} min to fit your time.")
        end = arrive + dt.timedelta(minutes=dur)
        stops.append(Stop(
            venue=v, start=arrive, end=end, duration_min=dur, route=route,
            availability=get_venue_availability(v, arrive, end), crowd=estimate_crowd_level(v, arrive),
            why=why.get(v.id, ""),
        ))
        travel += route.duration_min
        cursor, prev = end + dt.timedelta(minutes=a["stop_buffer_min"]), p
    ret = route_fn(prev, start_point, stops[-1].end, prefs.travel_mode) if stops else None
    if ret:
        notes.append("Return journey is costed but not scheduled.")
    return Itinerary(
        variant=variant, departs_at=start, origin=start_point, stops=stops, return_route=ret,
        total_travel_min=round(travel, 1), notes=notes,
    )
