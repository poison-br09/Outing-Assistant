"""Tool 7: getActivityOptions. Discovers candidate activities from the place provider near the user."""
import logging

from pydantic import BaseModel

from ..config import get_assumptions
from ..schemas.context import Location, Point, TemporalContext
from ..schemas.itinerary import Venue
from ..schemas.preferences import Preferences
from ..services.geo import ProviderError, search_places
from ..utils import has_category
from ._venues import to_venue

log = logging.getLogger(__name__)


class ActivityResult(BaseModel):
    venues: list[Venue] = []
    warnings: list[str] = []
    relaxed: list[str] = []
    provider_error: bool = False


def search_radius_m(prefs: Preferences, temporal: TemporalContext) -> int:
    """Radius that keeps travel a small share of the available time (locality matters)."""
    a = get_assumptions()
    mode = prefs.travel_mode
    speed = a["speeds_kmh"].get(mode, a["speeds_kmh"]["taxi"] if mode == "flexible" else 5)
    km = speed * (temporal.duration_minutes / 60) * a["travel_time_share"] / a["road_factor"]
    cap = a["max_radius_km"][mode]
    if (mc := prefs.constraint("max_distance")) and mc.value:
        cap = min(cap, mc.value)
    return int(max(min(km, cap), min(a["min_radius_km"], cap)) * 1000)


def get_activity_options(
    location: Location, start: Point, areas: list[Point], prefs: Preferences,
    temporal: TemporalContext, radius_m: int,
) -> ActivityResult:
    a = get_assumptions()
    res = ActivityResult()
    centers = [start, *areas]
    groups = [(i, a["interest_categories"][i]) for i in prefs.interests if i in a["interest_categories"]]
    for i in prefs.interests:
        if i not in a["interest_categories"]:
            res.warnings.append(f"No venue category for interest '{i}'; it was not used for discovery.")

    found: dict[str, Venue] = {}
    errors = 0

    def run(search_groups: list[tuple[str, list[str]]]) -> None:
        nonlocal errors
        for interest, cats in search_groups:
            for c in centers:
                try:
                    props = search_places(cats, c.lat, c.lon, radius_m, a["places_limit"])
                except ProviderError as e:
                    errors += 1
                    log.warning("place search failed: %s", e)
                    res.provider_error = True
                    res.warnings.append(f"Venue search for '{interest}' failed ({e.message}).")
                    continue
                for p in props:
                    v = to_venue(p, "activity", start, interest if interest != "general" else None)
                    if not v or v.id in prefs.excluded_venue_ids or v.id in found:
                        continue
                    if has_category(v.categories, ["catering"]) and not has_category(cats, ["catering"]):
                        continue
                    if v.est_cost_per_person * prefs.group_size > prefs.total_budget:
                        continue
                    found[v.id] = v

    run(groups or [("general", a["default_activity_categories"])])
    if not found and groups and not res.provider_error:
        res.relaxed.append("No venues matched your interests, so I widened the search to general attractions.")
        run([("general", a["default_activity_categories"])])
    res.provider_error = res.provider_error and not found
    res.venues = sorted(found.values(), key=lambda v: v.distance_m)
    return res
