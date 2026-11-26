"""Tool 13: validatePlan. Deterministic checks; failures block a plan, warnings are shown to the user."""
from ..config import get_assumptions
from ..schemas.context import TemporalContext, WeatherReport
from ..schemas.itinerary import Check, CostEstimate, Itinerary, ValidationResult
from ..schemas.preferences import Preferences
from ..utils import has_category
from .food_finder import required_diet


def validate_plan(
    it: Itinerary, cost: CostEstimate, prefs: Preferences, temporal: TemporalContext,
    weather: WeatherReport | None, accept_extra_budget: bool = False,
) -> ValidationResult:
    a = get_assumptions()
    cur = cost.currency
    checks: list[Check] = []

    def add(name: str, status: str, msg: str) -> None:
        checks.append(Check(name=name, status=status, message=msg))  # type: ignore[arg-type]

    if not it.stops:
        add("itinerary", "fail", "No stops fit into the available time.")
        return ValidationResult(passed=False, checks=checks)

    # budget
    requires = False
    if cost.within_budget:
        add("budget", "pass", f"Estimated {cur}{cost.total:,.0f} is within your {cur}{cost.budget:,.0f} budget.")
    elif cost.within_ceiling:
        pct = cost.over_by / cost.budget
        flex = prefs.budget_flexibility
        msg = f"Estimated {cur}{cost.total:,.0f} is {cur}{cost.over_by:,.0f} ({pct:.0%}) over budget, within the 10% ceiling."
        if flex == "yes" or (flex == "ask" and accept_extra_budget):
            add("budget", "warn", msg + " You allowed this extra spend.")
        elif flex == "ask":
            requires = True
            add("budget", "warn", msg + " Needs your approval.")
        else:
            add("budget", "fail", msg + " You asked to stay strictly within budget.")
    else:
        add("budget", "fail", f"Estimated {cur}{cost.total:,.0f} exceeds the allowed ceiling of {cur}{cost.ceiling:,.0f}.")

    # timing
    if it.departs_at < temporal.earliest_start or it.ends_at > temporal.latest_end:
        add("timing", "fail", f"Plan runs {it.departs_at:%H:%M}-{it.ends_at:%H:%M}, outside your allowed window.")
    else:
        add("timing", "pass", f"Plan runs {it.departs_at:%H:%M}-{it.ends_at:%H:%M}, inside your "
            + ("exact" if temporal.hard_window else "allowed") + " window.")

    # travel
    span = max((it.ends_at - it.departs_at).total_seconds() / 60, 1)
    share = it.total_travel_min / span
    long_legs = [s for s in it.stops if s.route and s.route.duration_min > a["validator"]["max_leg_min"]]
    if share > a["validator"]["max_travel_share"] or long_legs:
        add("travel", "warn", f"Travel is {it.total_travel_min:.0f} min ({share:.0%} of the outing)"
            + (f"; {len(long_legs)} leg(s) exceed {a['validator']['max_leg_min']} min." if long_legs else "."))
    else:
        add("travel", "pass", f"Travel is {it.total_travel_min:.0f} min ({share:.0%} of the outing).")

    # opening hours
    closed = [s.venue.name for s in it.stops if s.availability and s.availability.status == "closed"]
    unverified = [s.venue.name for s in it.stops if not s.availability or s.availability.status == "unverified"]
    if closed:
        add("opening_hours", "fail", "Closed at the planned time: " + ", ".join(closed) + ".")
    elif unverified:
        add("opening_hours", "warn", f"Opening hours unverified for {len(unverified)} stop(s): please confirm before going.")
    else:
        add("opening_hours", "pass", "Provider opening hours cover every stop (not live-verified).")

    # hard constraints
    diet = required_diet(prefs)
    foods = [s for s in it.stops if s.venue.kind == "food"]
    if diet and any(not s.venue.diet_verified for s in foods):
        add("diet", "warn", f"{diet.capitalize()} suitability is unverified for the meal stop: confirm with the venue.")
    elif diet:
        add("diet", "pass", f"Meal stop is tagged {diet} in the place data.")
    if (prefs.has_constraint("alcohol_free") or prefs.has_constraint("family_friendly")) and any(
            has_category(s.venue.categories, a["alcohol_categories"]) for s in it.stops):
        add("alcohol", "fail", "Plan includes a bar/pub despite your constraints.")
    if prefs.has_constraint("avoid_outdoor") and any(not s.venue.indoor for s in it.stops):
        add("outdoor", "fail", "Plan includes an outdoor stop despite your constraint.")
    if prefs.has_constraint("accessibility"):
        add("accessibility", "warn", "Provider data has no accessibility details: verify access with each venue.")

    # weather & crowds
    outdoor = [s for s in it.stops if not s.venue.indoor]
    if weather is None or not weather.available:
        add("weather", "warn", "No weather forecast available; plan leans conservative.")
    elif weather.is_rainy and outdoor:
        add("weather", "warn", "Rain is forecast and the plan has outdoor stops: carry an umbrella.")
    elif weather.is_hot and outdoor:
        add("weather", "warn", "It will be hot and the plan has outdoor stops: carry water.")
    else:
        add("weather", "pass", f"Forecast: {weather.summary}.")
    busy = [s.venue.name for s in it.stops if s.crowd and s.crowd.level == "high"]
    if prefs.has_constraint("avoid_crowds") and busy:
        add("crowds", "warn", "May be busy (heuristic estimate): " + ", ".join(busy) + ".")
    else:
        add("crowds", "pass", "Crowd levels are heuristic estimates, not live data.")

    add("data_quality", "pass", "Prices are estimates; venue details come from a place provider and are not live-verified.")
    return ValidationResult(
        passed=not any(c.status == "fail" for c in checks), requires_budget_approval=requires, checks=checks
    )
