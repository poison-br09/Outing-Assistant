"""Agent orchestrator: runs the tools in order, records a trace, and degrades gracefully.

Stateless: the client keeps the proposal and sends it back to /finalize for approval.
"""
import datetime as dt
import logging
from typing import Optional
from zoneinfo import ZoneInfo

from ..config import get_assumptions
from ..schemas.api import AgentResponse, FinalizeRequest, FinalPlan, LLMInfo, PlanRequest
from ..schemas.context import Location, Point, RouteEstimate, TemporalContext, WeatherReport
from ..schemas.itinerary import Itinerary, PlanProposal, RankedVenue, Venue
from ..schemas.preferences import Preferences
from ..services.geo import ProviderError, geocode_place, resolve_city
from ..tools.activity_finder import get_activity_options, search_radius_m
from ..tools.activity_ranker import rank_activities
from ..tools.cost_estimator import estimate_cost
from ..tools.final_plan_generator import generate_final_plan
from ..tools.food_finder import get_food_options
from ..tools.itinerary_optimizer import optimise_itinerary
from ..tools.itinerary_planner import select_for_variant, stop_duration
from ..tools.plan_validator import validate_plan
from ..tools.preference_parser import parse_user_preferences
from ..tools.temporal_resolver import candidate_starts, resolve_temporal_context
from ..tools.traffic_tool import get_traffic_report
from ..tools.venue_availability import get_venue_availability
from ..tools.weather_tool import get_weather_report
from .fallbacks import generic_suggestions
from .narrator import explain_proposal
from .trace import Trace

log = logging.getLogger(__name__)

VARIANTS = [
    ("budget_friendly", "Budget-friendly"),
    ("balanced", "Balanced"),
    ("experience_first", "Experience-first"),
]


def llm_note(info: Optional[LLMInfo]) -> str:
    if info is None:
        return "no free text, LLM not needed"
    if info.used:
        return f"LLM {info.model} ({info.attempts} attempt(s), {info.latency_ms} ms)"
    return f"LLM unavailable ({info.fallback_reason}); deterministic logic used, reduced AI functionality"


class _Run:
    def __init__(self, req: PlanRequest):
        self.req = req
        self.trace = Trace()
        self.llm: list[LLMInfo] = []
        self.warnings: list[str] = []
        self.assumptions: list[str] = []
        self.partial: dict = {}
        self.prefs: Optional[Preferences] = None
        self.temporal: Optional[TemporalContext] = None
        self.weather: Optional[WeatherReport] = None

    def respond(self, status: str, message: str = "", **kw) -> AgentResponse:
        return AgentResponse(
            status=status, message=message,  # type: ignore[arg-type]
            partial=self.partial, preferences=self.prefs, temporal=self.temporal, weather=self.weather,
            warnings=list(dict.fromkeys(self.warnings)), assumptions=self.assumptions, llm=self.llm,
            trace=self.trace.steps, currency=get_assumptions()["currency"], **kw,
        )

    def fallback(self, message: str) -> AgentResponse:
        return self.respond("fallback", message, generic_suggestions=generic_suggestions(self.prefs))


def _never_open(v: Venue, t: TemporalContext) -> bool:
    """True only when provider hours say the venue is closed for every allowed start time."""
    dur = dt.timedelta(minutes=stop_duration(v))
    return all(
        get_venue_availability(v, s, s + dur).status == "closed" for s in candidate_starts(t)
    )


def _centroid(venues: list[Venue], fallback: Point) -> Point:
    if not venues:
        return fallback
    return Point(name="activity area", lat=sum(v.lat for v in venues) / len(venues),
                 lon=sum(v.lon for v in venues) / len(venues))


class Orchestrator:
    # ------------------------------------------------------------------ plan
    def run(self, req: PlanRequest) -> AgentResponse:
        r = _Run(req)
        try:
            return self._run(r)
        except Exception as e:  # last-resort safety net: always return something useful
            log.exception("agent run failed")
            r.warnings.append(f"An unexpected error occurred ({type(e).__name__}).")
            return r.fallback("Something went wrong while planning, so here are generic ideas instead.")

    def _run(self, r: _Run) -> AgentResponse:
        a = get_assumptions()
        req, trace = r.req, r.trace

        # 1. preferences
        with trace.step("Preferences parsed") as st:
            parsed = parse_user_preferences(req.fields, req.message, req.message_overrides)
            if parsed.llm:
                r.llm.append(parsed.llm)
            r.partial, r.assumptions = parsed.partial, parsed.assumptions
            if parsed.preferences is None:
                st.warn(f"Need more information: {', '.join(parsed.missing) or 'clarification'}. {llm_note(parsed.llm)}")
            else:
                p = parsed.preferences
                st.ok(f"{p.group_type} of {p.group_size}, budget {a['currency']}{p.total_budget:,.0f}, "
                      f"{len(p.constraints)} constraint(s) ({sum(c.kind == 'hard' for c in p.constraints)} hard); "
                      f"{llm_note(parsed.llm)}")
        if parsed.preferences is None:
            return r.respond("needs_clarification", "I need a little more information to plan this.",
                             questions=parsed.questions)
        prefs = parsed.preferences.model_copy(update={"excluded_venue_ids": req.excluded_venue_ids})
        r.prefs = prefs

        # 2. temporal context
        with trace.step("Temporal context resolved") as st:
            try:
                loc = resolve_city(prefs.city)
            except ProviderError as e:
                st.fail(f"Location service unavailable ({e.message}).")
                r.warnings.append("Could not look up the city, so no venue search was possible.")
                return r.fallback("I couldn't look up your city right now, so here are generic ideas.")
            if loc is None:
                st.warn(f"City '{prefs.city}' not found.")
                return r.respond("needs_clarification", f"I couldn't find a city called '{prefs.city}'.",
                                 questions=["Which city (or nearby larger city) should I plan in?"])
            temporal = resolve_temporal_context(prefs, loc)
            r.temporal = temporal
            today = dt.datetime.now(ZoneInfo(temporal.timezone)).date()
            if temporal.date < today:
                st.warn(f"Date {temporal.date} is in the past.")
                return r.respond("needs_clarification", "That date has already passed.",
                                 questions=["Which upcoming date should I plan for?"])
            st.ok(f"{temporal.date:%a %d %b %Y} ({temporal.timezone}), {temporal.timing_mode} timing, "
                  f"{temporal.earliest_start:%H:%M}-{temporal.latest_end:%H:%M}, {temporal.duration_minutes} min plan")

        # 3. weather
        with trace.step("Weather checked") as st:
            r.weather = get_weather_report(loc, temporal, today)
            if r.weather.available:
                st.ok(f"{r.weather.summary} (Open-Meteo forecast)")
            else:
                st.warn(r.weather.warnings[0] if r.weather.warnings else "Weather unavailable")
                r.warnings += r.weather.warnings

        # 4. activities
        with trace.step("Activities discovered") as st:
            center = Point(name=loc.name, lat=loc.lat, lon=loc.lon)
            start_pt = center
            notes: list[str] = []
            if prefs.starting_location:
                try:
                    g = geocode_place(f"{prefs.starting_location}, {prefs.city}", loc)
                    if g:
                        start_pt = Point(name=prefs.starting_location, lat=g[0], lon=g[1])
                    else:
                        notes.append(f"Couldn't locate '{prefs.starting_location}': using the city centre.")
                except ProviderError as e:
                    notes.append(f"Couldn't locate your starting point ({e.message}): using the city centre.")
            areas: list[Point] = []
            for area in prefs.preferred_areas:
                try:
                    g = geocode_place(f"{area}, {prefs.city}", loc)
                    if g:
                        areas.append(Point(name=area, lat=g[0], lon=g[1]))
                except ProviderError:
                    break
            radius = search_radius_m(prefs, temporal)
            found = get_activity_options(loc, start_pt, areas, prefs, temporal, radius)
            r.warnings += notes + found.warnings + found.relaxed
            if not found.venues:
                msg = ("Venue data is unavailable right now." if found.provider_error
                       else "No venues matched within a practical distance.")
                st.fail(msg)
                return r.fallback(msg + " Here are generic ideas instead; no venue names are invented.")
            st.ok(f"{len(found.venues)} candidates within {radius / 1000:.1f} km of {start_pt.name} (Geoapify)")
            if found.relaxed:
                st.warn(f"{len(found.venues)} candidates after relaxing interests: {found.relaxed[0]}")

        # 5. ranking (includes crowd heuristics)
        with trace.step("Activities ranked") as st:
            ranked = rank_activities(found.venues, prefs, r.weather, temporal, radius)
            if not ranked:
                st.fail("Every candidate violated a hard constraint.")
                r.warnings.append("No venue satisfies all of your hard constraints.")
                return r.fallback("No venues satisfy all your hard constraints, and I won't relax those.")
            st.ok(f"Top: {', '.join(x.venue.name for x in ranked[:3])} (mood, interest, location, weather, group)")

        # 6. traffic
        route_cache: dict = {}

        def route_fn(o: Point, d: Point, t: dt.datetime, m: str) -> RouteEstimate:
            key = (round(o.lat, 4), round(o.lon, 4), round(d.lat, 4), round(d.lon, 4),
                   t.replace(minute=(t.minute // 30) * 30, second=0, microsecond=0), m)
            if key not in route_cache:
                route_cache[key] = get_traffic_report(o, d, t, m)
            return route_cache[key]

        with trace.step("Traffic checked") as st:
            probes = [route_fn(start_pt, venue_point, temporal.window_start, prefs.travel_mode)
                      for venue_point in (Point(name=x.venue.name, lat=x.venue.lat, lon=x.venue.lon) for x in ranked[:3])]
            heavy = any(p.traffic_condition == "heavy" for p in probes)
            live = all(p.source == "google_routes" for p in probes)
            avg = sum(p.duration_min for p in probes) / len(probes)
            if heavy:
                radius = int(radius * 0.7)
            msg = (f"avg {avg:.0f} min to top candidates, {'heavy' if heavy else 'normal'} congestion expected"
                   + ("; keeping stops closer together" if heavy else ""))
            if live:
                st.ok(msg + " (traffic-aware Google Routes)")
            else:
                st.warn(msg + " (ESTIMATE from distance and time of day, not live traffic)")
                r.warnings.append("Travel times are estimates, not live traffic.")

        # 7. crowd summary
        with trace.step("Crowd context estimated") as st:
            levels = [x.crowd.level for x in ranked[:5] if x.crowd]
            st.warn("Heuristic only: " + ", ".join(f"{levels.count(l)} {l}" for l in ("low", "moderate", "high") if l in levels)
                    + " among top candidates (not live crowd data)")

        # 8. availability pre-filter
        with trace.step("Venue availability checked") as st:
            before = len(ranked)
            ranked = [x for x in ranked if not _never_open(x.venue, temporal)]
            unverified = sum(1 for x in ranked if not x.venue.opening_hours)
            msg = f"Dropped {before - len(ranked)} venue(s) closed throughout your window"
            if unverified:
                st.warn(f"{msg}; {unverified} of {len(ranked)} have no hours data (kept, flagged as unverified)")
            else:
                st.ok(msg + " (provider hours, not live-verified)")
            if not ranked:
                return r.fallback("All matching venues are closed during your window.")

        # 9. food
        with trace.step("Food options retrieved") as st:
            anchor = _centroid([x.venue for x in ranked[:3]], start_pt)
            food = get_food_options(anchor, prefs, prefs.total_budget, temporal, radius)
            foods = [f for f in food.venues if not _never_open(f.venue, temporal)]
            r.warnings += food.warnings + food.relaxed
            if foods and not food.relaxed:
                st.ok(f"{len(foods)} options respecting diet, alcohol and budget constraints")
            elif foods:
                st.warn(f"{len(foods)} options, but {food.relaxed[0]}")
            elif temporal.duration_minutes >= a["min_duration_for_meal_min"]:
                st.warn("No suitable food options found: plans will have no meal stop.")
                r.warnings.append("No food option matched your constraints and budget, so no meal stop was planned.")
            else:
                st.ok("Short outing: no meal stop planned")

        # 10-13. build, optimise, cost, validate
        working = self._build_variants(r, VARIANTS, ranked, foods, prefs, temporal, radius, start_pt, route_fn)
        proposals = [p for p in working if p.validation.passed]
        if not proposals:
            r.warnings += [c.message for p in working for c in p.validation.failures]
            r.warnings.append("No standard plan fit your constraints, so a low-cost free-first plan was tried.")
            low = self._build_variants(r, [("low_cost", "Low-cost (free-first)")], ranked, foods, prefs,
                                       temporal, radius, start_pt, route_fn)
            proposals = [p for p in low if p.validation.passed]
        if not proposals:
            r.warnings += [c.message for p in low for c in p.validation.failures]
            return r.fallback("I couldn't build a plan that satisfies your budget, time and constraints. "
                              "Try a bigger budget, a longer window or fewer hard constraints.")

        self._finish_proposals(proposals, ranked, prefs)
        rec = self._recommend(proposals)

        # 14. explanations
        with trace.step("Explanations written") as st:
            better, info = explain_proposal(rec, prefs)
            r.llm.append(info)
            for s in rec.itinerary.stops:
                if s.venue.id in better:
                    s.why = better[s.venue.id]
            (st.ok if info.used else st.warn)(llm_note(info))

        with trace.step("Awaiting user approval") as st:
            st.ok(f"{len(proposals)} plan(s) proposed; recommended: {rec.title}. Nothing is final until you approve.")
        msg = "Here are your plans. Review the trade-offs and approve one to finalise."
        if rec.variant == "low_cost":
            msg = "Your budget is tight for a full outing, so this is a free-first plan. " + msg
        return r.respond("proposal", msg, proposals=proposals, recommended_id=rec.id)

    # ------------------------------------------------------------ pipeline
    def _build_variants(self, r, variants, ranked, foods, prefs, temporal, radius, start_pt, route_fn):
        trace = r.trace
        selected: list[tuple[str, str, list[Venue], Optional[Venue], dict]] = []
        with trace.step("Candidate plans built") as st:
            seen: set = set()
            for key, title in variants:
                acts, food, why = select_for_variant(key, ranked, foods, prefs, temporal, radius)
                sig = (tuple(v.id for v in acts), food.id if food else None)
                if not acts or sig in seen:
                    continue
                seen.add(sig)
                selected.append((key, title, acts, food, why))
            if len(selected) < len(variants):
                r.warnings.append("Some plan styles chose identical stops, so fewer alternatives are shown.")
            if selected:
                st.ok(f"{len(selected)} distinct candidate plan(s): " + ", ".join(t for _, t, *_ in selected))
            else:
                st.warn("No candidate plan could be built from the available venues for this plan style.")

        with trace.step("Itinerary optimised") as st:
            its: list[tuple[str, str, Itinerary]] = []
            for key, title, acts, food, why in selected:
                it = optimise_itinerary(acts, food, temporal, prefs, r.weather, start_pt, route_fn, key, why)
                its.append((key, title, it))
            st.ok("Compared stop orders and start times on travel, weather, crowds, opening hours and meal timing")

        with trace.step("Candidate plans costed") as st:
            costs = [estimate_cost(it, prefs) for _, _, it in its]
            st.ok(", ".join(f"{t}: {c.currency}{c.total:,.0f}" for (_, t, _), c in zip(its, costs))
                  + " (all estimates; activity, food, transport)")

        with trace.step("Plans validated") as st:
            out: list[PlanProposal] = []
            for (key, title, it), cost in zip(its, costs):
                val = validate_plan(it, cost, prefs, temporal, r.weather)
                out.append(PlanProposal(
                    id=key, variant=key, title=title, itinerary=it, cost=cost, validation=val,
                    requires_budget_approval=val.requires_budget_approval,
                ))
            ok = sum(p.validation.passed for p in out)
            (st.ok if ok == len(out) else st.warn)(
                f"{ok}/{len(out)} plans passed budget, timing, hours and hard-constraint checks")
        return out

    @staticmethod
    def _finish_proposals(proposals: list[PlanProposal], ranked: list[RankedVenue], prefs: Preferences) -> None:
        score = {x.venue.id: x.score for x in ranked}
        cheapest = min(proposals, key=lambda p: p.cost.total)
        cur = cheapest.cost.currency
        for p in proposals:
            scores = [score[s.venue.id] for s in p.itinerary.stops if s.venue.id in score]
            p.score = round(sum(scores) / len(scores), 3) if scores else 0.0
            t = p.tradeoffs
            if p.requires_budget_approval:
                t.append(f"{cur}{p.cost.over_by:,.0f} over your budget (within the 10% ceiling): needs your approval.")
            if p is not cheapest and p.cost.total > cheapest.cost.total:
                t.append(f"Costs about {cur}{p.cost.total - cheapest.cost.total:,.0f} more than the {cheapest.title} plan.")
            if p.variant == "experience_first":
                t.append("Favours the highest-scoring venues and a pricier meal over cost.")
            if p.variant == "budget_friendly":
                t.append("Favours free and low-cost stops; may trade some fit with your mood for savings.")
            others = [q for q in proposals if q is not p]
            if others and p.itinerary.total_travel_min > min(q.itinerary.total_travel_min for q in others) + 10:
                t.append("Involves more travel time than the alternatives.")
            t += [w.message for w in p.validation.warnings if w.name in ("diet", "opening_hours", "crowds")]

    @staticmethod
    def _recommend(proposals: list[PlanProposal]) -> PlanProposal:
        safe = [p for p in proposals if not p.requires_budget_approval] or proposals
        balanced = next((p for p in safe if p.variant == "balanced"), None)
        return balanced or max(safe, key=lambda p: p.score)

    # -------------------------------------------------------------- finalize
    def finalize(self, req: FinalizeRequest) -> FinalPlan:
        trace = Trace()
        with trace.step("Approval received") as st:
            if not req.approved:
                raise PermissionError("The plan must be approved before it can be finalised.")
            st.ok(f"User approved '{req.proposal.title}'")
        with trace.step("Plan re-validated") as st:
            cost = estimate_cost(req.proposal.itinerary, req.preferences)
            val = validate_plan(req.proposal.itinerary, cost, req.preferences, req.temporal, req.weather,
                                req.accept_extra_budget)
            if not val.passed:
                raise ValueError("Plan no longer valid: " + "; ".join(c.message for c in val.failures))
            if val.requires_budget_approval:
                raise ValueError("This plan is over your budget and needs your explicit approval of the extra spend.")
            st.ok(f"Total {cost.currency}{cost.total:,.0f} re-checked against budget, timing and constraints")
        proposal = req.proposal.model_copy(update={"cost": cost, "validation": val})
        with trace.step("Final plan generated") as st:
            plan = generate_final_plan(proposal, req.preferences, req.temporal, req.weather, req.approved)
            (st.ok if plan.llm.used else st.warn)(llm_note(plan.llm))
        plan.trace = trace.steps
        return plan
