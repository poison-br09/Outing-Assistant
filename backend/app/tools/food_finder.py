"""Tool 9: getFoodOptions. Dietary constraints are hard: unverified diet data is flagged, never presented as verified."""
import logging

from pydantic import BaseModel

from ..config import get_assumptions
from ..schemas.context import Point, TemporalContext
from ..schemas.itinerary import RankedVenue, Venue
from ..schemas.preferences import Preferences
from ..services.geo import ProviderError, search_places
from ..utils import category_lookup, has_category
from ._venues import to_venue
from .activity_ranker import mood_profile

log = logging.getLogger(__name__)


class FoodResult(BaseModel):
    venues: list[RankedVenue] = []
    warnings: list[str] = []
    relaxed: list[str] = []
    provider_error: bool = False


def required_diet(prefs: Preferences) -> str | None:
    return "vegan" if prefs.has_constraint("vegan") else "vegetarian" if prefs.has_constraint("vegetarian") else None


def get_food_options(
    anchor: Point, prefs: Preferences, food_budget: float, temporal: TemporalContext, radius_m: int,
) -> FoodResult:
    a = get_assumptions()
    res = FoodResult()
    cats = list(a["food_categories"])
    no_alcohol = prefs.has_constraint("alcohol_free") or prefs.has_constraint("family_friendly") or prefs.group_type == "family"
    diet = required_diet(prefs)

    def search(conditions: list[str] | None) -> dict[str, Venue]:
        out: dict[str, Venue] = {}
        try:
            props = search_places(cats, anchor.lat, anchor.lon, radius_m, a["places_limit"], conditions)
        except ProviderError as e:
            res.provider_error = True
            res.warnings.append(f"Food search failed ({e.message}).")
            return out
        for p in props:
            v = to_venue(p, "food", anchor, "food" if "food" in prefs.interests else None)
            if not v or v.id in prefs.excluded_venue_ids:
                continue
            if no_alcohol and has_category(v.categories, a["alcohol_categories"]):
                continue
            if v.est_cost_per_person * prefs.group_size > food_budget:
                continue
            out[v.id] = v
        return out

    found = search([diet] if diet else None)
    if diet and found:
        for v in found.values():  # the provider filtered on this diet condition
            v.diet_verified = True
            if diet not in v.diet_tags:
                v.diet_tags.append(diet)
    elif diet and not found and not res.provider_error:
        found = search(None)
        if found:
            res.relaxed.append(
                f"No place in the data is tagged '{diet}'. These restaurants have UNVERIFIED diet information, "
                f"so please confirm {diet} options with the venue before going."
            )
    res.provider_error = res.provider_error and not found

    profile = mood_profile(prefs.mood)
    for v in found.values():
        prox = max(0.0, 1 - v.distance_m / max(radius_m, 1))
        mood = category_lookup(profile["boosts"], v.categories, 0.5)
        dietf = 1.0 if (not diet or v.diet_verified) else 0.3
        interest = 1.0 if v.matched_interest else 0.5
        score = 0.45 * prox + 0.25 * mood + 0.20 * dietf + 0.10 * interest
        reasons = [f"About {v.distance_m / 1000:.1f} km from your route."]
        if diet and v.diet_verified:
            reasons.insert(0, f"Tagged {diet} in the place data.")
        elif diet:
            reasons.insert(0, f"{diet.capitalize()} options unverified: confirm with the venue.")
        res.venues.append(RankedVenue(venue=v, score=round(score, 4), components={"prox": prox, "mood": mood}, reasons=reasons))
    res.venues.sort(key=lambda r: r.score, reverse=True)
    return res
