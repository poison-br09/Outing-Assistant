"""Tool 14: generateFinalPlan. Runs only after explicit approval; never claims a booking was made."""
import json
from typing import Optional

from pydantic import BaseModel

from ..agent.narrator import prefs_facts
from ..agent.prompts import OVERVIEW_SYSTEM
from ..llm.client import try_llm
from ..schemas.api import FinalPlan, TimelineEntry
from ..schemas.context import TemporalContext, WeatherReport
from ..schemas.itinerary import PlanProposal
from ..schemas.preferences import Preferences


class _Overview(BaseModel):
    overview: str
    practical_notes: list[str] = []


def _practical_notes(p: PlanProposal, weather: Optional[WeatherReport]) -> list[str]:
    notes = ["Leave on time: the schedule includes travel and short buffers between stops."]
    if weather and weather.available:
        if weather.is_rainy:
            notes.append("Rain is possible: carry an umbrella.")
        if weather.is_hot:
            notes.append("It may be hot: carry water and sunscreen for outdoor stops.")
    if any(s.route and s.route.source == "estimate" for s in p.itinerary.stops):
        notes.append("Travel times are estimates, not live traffic: check your maps app before leaving.")
    return notes


def _caveats(p: PlanProposal, weather: Optional[WeatherReport]) -> list[str]:
    out = ["No booking has been made: this is a plan, not a reservation.",
           "Prices are estimates, not quotes."]
    out += [c.message for c in p.validation.checks if c.status == "warn"]
    if weather is None or not weather.available:
        out.append("Weather forecast was unavailable.")
    out += p.itinerary.notes
    return list(dict.fromkeys(out))


def generate_final_plan(
    proposal: PlanProposal, prefs: Preferences, temporal: TemporalContext,
    weather: Optional[WeatherReport], approved: bool,
) -> FinalPlan:
    if not approved:
        raise PermissionError("The plan must be approved before it can be finalised.")
    it, cost = proposal.itinerary, proposal.cost
    timeline = []
    for s in it.stops:
        r = s.route
        timeline.append(TimelineEntry(
            time=f"{s.start:%H:%M}-{s.end:%H:%M}", title=s.venue.name, kind=s.venue.kind,
            address=s.venue.address, duration_min=s.duration_min, why=s.why,
            map_link=f"https://www.google.com/maps/dir/?api=1&destination={s.venue.lat},{s.venue.lon}",
            travel_note=(f"{r.duration_min:.0f} min {r.mode.replace('_', ' ')} ({r.distance_km:.1f} km"
                         + (", estimate" if r.source == "estimate" else ", traffic-aware") + ")") if r else None,
            availability=s.availability.status if s.availability else "unverified",
        ))
    notes, caveats = _practical_notes(proposal, weather), _caveats(proposal, weather)

    facts = {
        "user": prefs_facts(prefs), "date": temporal.date.isoformat(), "plan": proposal.title,
        "stops": [f"{t.time} {t.title}" for t in timeline],
        "total_cost": f"{cost.currency}{cost.total:,.0f}", "weather": weather.summary if weather else "unknown",
    }
    data, info = try_llm(OVERVIEW_SYSTEM, json.dumps(facts), _Overview)
    if data is not None:
        overview, notes = data.overview, list(dict.fromkeys(notes + data.practical_notes))[:6]
    else:
        overview = (f"{proposal.title}: {len(it.stops)} stops on {temporal.date:%A %d %b}, "
                    f"{it.departs_at:%H:%M}-{it.ends_at:%H:%M}, about {cost.currency}{cost.total:,.0f} for {prefs.group_size}.")
    return FinalPlan(
        overview=overview, timeline=timeline,
        cost_lines=[l.model_dump() for l in cost.lines], total_cost=cost.total, per_person=cost.per_person,
        currency=cost.currency, practical_notes=notes, caveats=caveats, llm=info,
    )
