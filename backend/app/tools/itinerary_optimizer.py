"""Tool 12: optimiseItinerary.

Searches stop order x allowed start times, scoring travel, weather, crowds, opening hours and
meal timing together (not distance alone). The search uses offline route estimates; the chosen
plan is then rebuilt with the real (traffic-aware when available) route function.
"""
import datetime as dt
from itertools import permutations
from typing import Optional

from ..config import get_assumptions
from ..schemas.context import Point, TemporalContext, WeatherReport
from ..schemas.itinerary import Itinerary, Venue
from ..schemas.preferences import Preferences
from .itinerary_planner import RouteFn, arrange, build_itinerary
from .temporal_resolver import candidate_starts
from .traffic_tool import estimate_route


def _meal_gap_min(t: dt.datetime) -> float:
    return min(abs((t - t.replace(hour=int(m["target"]), minute=0)).total_seconds()) / 60
               for m in get_assumptions()["meals"].values())


def _score(it: Itinerary, expected: int, weather: Optional[WeatherReport], prefs: Preferences,
           temporal: TemporalContext, start: dt.datetime) -> float:
    hot = get_assumptions()["weather"]["hot_c"]
    quiet_w = 0.4 if prefs.has_constraint("avoid_crowds") else 0.05
    sc = it.total_travel_min + 200 * (expected - len(it.stops))
    for s in it.stops:
        outdoor = not s.venue.indoor
        if s.availability and s.availability.status == "closed":
            sc += 1000
        elif s.availability and s.availability.status == "unverified":
            sc += 3
        if weather and weather.available and (hw := weather.hour_at(s.start)):
            sc += (hw.precip_prob or 0) / 100 * s.duration_min * (0.6 if outdoor else 0.1)
            if outdoor and (hw.temp_c or 0) >= hot:
                sc += s.duration_min * 0.5
        sc += (s.crowd.score or 0) * s.duration_min * quiet_w if s.crowd else 0
        if s.venue.kind == "food":
            sc += _meal_gap_min(s.start) * 0.3
    return sc + abs((start - temporal.window_start).total_seconds()) / 60 * 0.05


def optimise_itinerary(
    acts: list[Venue], food: Optional[Venue], temporal: TemporalContext, prefs: Preferences,
    weather: Optional[WeatherReport], start_point: Point, route_fn: RouteFn, variant: str,
    why: Optional[dict[str, str]] = None,
) -> Itinerary:
    starts = candidate_starts(temporal)
    orders = list(permutations(acts)) if len(acts) <= 4 else [tuple(acts)]
    expected = len(acts) + (1 if food else 0)
    best: Optional[tuple[float, dt.datetime, tuple[Venue, ...]]] = None
    for start in starts:
        for perm in orders:
            it = build_itinerary(arrange(list(perm), food, start), start, start_point, temporal,
                                 prefs, estimate_route, variant, why)
            sc = _score(it, expected, weather, prefs, temporal, start)
            if best is None or sc < best[0]:
                best = (sc, start, perm)
    assert best is not None  # starts is never empty
    _, start, perm = best
    result = build_itinerary(arrange(list(perm), food, start), start, start_point, temporal,
                             prefs, route_fn, variant, why)
    if start != temporal.window_start and not temporal.hard_window:
        result.notes.append(
            f"Start set to {start:%H:%M} after comparing forecast, crowd and traffic estimates across your allowed window."
        )
    if list(perm) != acts and len(acts) > 1:
        result.notes.append("Stops reordered to cut unnecessary travel.")
    return result
