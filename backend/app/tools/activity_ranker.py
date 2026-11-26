"""Tool 8: rankActivities. Hard constraints are filters; everything else is a weighted score with reasons."""
import datetime as dt
from typing import Optional

from ..config import get_assumptions
from ..schemas.context import TemporalContext, WeatherReport
from ..schemas.itinerary import RankedVenue, Venue
from ..schemas.preferences import Preferences
from ..utils import category_lookup, has_category
from .crowd_estimator import estimate_crowd_level


def mood_profile(mood: str) -> dict:
    low = mood.lower()
    for p in get_assumptions()["mood_profiles"]:
        if any(k in low for k in p["keywords"]):
            return p
    return {"keywords": [], "low_crowd": False, "boosts": {}}


def weather_score(v: Venue, weather: Optional[WeatherReport]) -> float:
    if weather is None or not weather.available:
        return 0.7 if v.indoor else 0.5  # conservative when unknown
    if weather.is_rainy:
        return 1.0 if v.indoor else 0.1
    if weather.is_hot:
        return 0.9 if v.indoor else 0.35
    return 0.7 if v.indoor else 0.9


def passes_hard_constraints(v: Venue, prefs: Preferences, radius_hint: bool = True) -> bool:
    a = get_assumptions()
    alcohol = has_category(v.categories, a["alcohol_categories"])
    if prefs.has_constraint("avoid_outdoor") and not v.indoor:
        return False
    if alcohol and (prefs.has_constraint("alcohol_free") or prefs.has_constraint("family_friendly")
                    or prefs.group_type == "family"):
        return False
    if (mc := prefs.constraint("max_distance")) and mc.value and v.distance_m > mc.value * 1000:
        return False
    if prefs.has_constraint("avoid_long_walks") and prefs.travel_mode in ("walking", "flexible") \
            and v.distance_m > a["long_walk_m"]:
        return False
    return True


def rank_activities(
    venues: list[Venue], prefs: Preferences, weather: Optional[WeatherReport],
    temporal: TemporalContext, radius_m: int,
) -> list[RankedVenue]:
    a = get_assumptions()
    w = a["scoring_weights"]
    profile = mood_profile(prefs.mood)
    group = a["group_profiles"].get(prefs.group_type, a["group_profiles"]["mixed"])
    mid = temporal.window_start + (temporal.window_end - temporal.window_start) / 2
    wants_quiet = prefs.has_constraint("avoid_crowds")
    ranked: list[RankedVenue] = []

    for v in venues:
        if not passes_hard_constraints(v, prefs):
            continue
        mood = category_lookup(profile["boosts"], v.categories, 0.5)
        interest = 1.0 if v.matched_interest else 0.3
        loc = max(0.0, 1 - v.distance_m / max(radius_m, 1))
        wx = weather_score(v, weather)
        grp = 0.2 if has_category(v.categories, group["avoid"]) else category_lookup(group["boosts"], v.categories, 0.6)
        crowd = estimate_crowd_level(v, mid)
        comps = {"mood": mood, "interest": interest, "location": loc, "weather": wx, "group": grp}
        score = sum(w[k] * comps[k] for k in comps)
        if weather and weather.available and weather.is_rainy and not v.indoor:
            score -= w["rain_outdoor_penalty"]  # rain: strongly prefer indoor stops
        if wants_quiet:
            score -= w["crowd_penalty"] * (crowd.score or 0)
        elif profile["low_crowd"]:
            score -= 0.4 * w["crowd_penalty"] * (crowd.score or 0)

        reasons = []
        if v.matched_interest:
            reasons.append(f"Matches your interest in {v.matched_interest}.")
        if mood >= 0.8 and prefs.mood:
            reasons.append(f"Suits your mood ('{prefs.mood[:40]}').")
        reasons.append(f"About {v.distance_m / 1000:.1f} km from your start.")
        if weather and weather.available and weather.is_rainy and v.indoor:
            reasons.append("Indoors, so the rain forecast does not matter.")
        elif weather and weather.available and not v.indoor and not weather.is_rainy and not weather.is_hot:
            reasons.append(f"Good weather for an outdoor stop ({weather.summary}).")
        if crowd.level == "low":
            reasons.append("Expected to be quiet then (heuristic estimate).")
        elif crowd.level == "high" and wants_quiet:
            reasons.append("May be busy (heuristic estimate), which is a trade-off against your wish to avoid crowds.")
        ranked.append(RankedVenue(venue=v, score=round(score, 4), components=comps, reasons=reasons, crowd=crowd))

    return sorted(ranked, key=lambda r: r.score, reverse=True)
