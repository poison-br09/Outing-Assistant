"""Tool 5: estimateCrowdLevel. Heuristic only (category x weekday x time-of-day); never presented as live."""
import datetime as dt

from ..config import get_assumptions
from ..schemas.context import CrowdEstimate
from ..schemas.itinerary import Venue
from ..utils import category_lookup


def estimate_crowd_level(venue: Venue, at: dt.datetime) -> CrowdEstimate:
    c = get_assumptions()["crowd"]
    score = category_lookup(c["base"], venue.categories, c["default_base"])
    parts = [f"base {score:.2f} for type"]
    if at.weekday() >= 5:
        score += c["weekend_boost"]
        parts.append("weekend")
    for p in c["peaks"]:
        if p["from"] <= at.hour < p["to"]:
            score += p["boost"]
            parts.append(f"peak hours {p['from']}-{p['to']}")
    score = round(min(score, 1.0), 2)
    level = "low" if score < c["low_below"] else "high" if score >= c["high_from"] else "moderate"
    return CrowdEstimate(
        level=level, confidence="low", score=score, is_heuristic=True,
        evidence="Heuristic estimate (" + ", ".join(parts) + "); not a live measurement.",
    )
