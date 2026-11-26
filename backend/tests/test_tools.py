import datetime as dt
from zoneinfo import ZoneInfo

from app.schemas.context import Point
from app.schemas.itinerary import Venue
from app.schemas.preferences import Preferences
from app.tools.cost_estimator import estimate_cost
from app.tools.itinerary_planner import build_itinerary
from app.tools.plan_validator import validate_plan
from app.tools.preference_parser import classify_constraints, parse_user_preferences
from app.tools.temporal_resolver import candidate_starts, resolve_temporal_context
from app.tools.traffic_tool import estimate_route
from app.tools.venue_availability import get_venue_availability, parse_opening_hours

from .conftest import BLR

IST = ZoneInfo("Asia/Kolkata")


def venue(vid="v1", hours="Mo-Su 06:00-23:00", cost=0.0, kind="activity", cats=None, lat=12.97, lon=77.64, **kw) -> Venue:
    return Venue(id=vid, name=vid, lat=lat, lon=lon, categories=cats or ["leisure.park"], kind=kind,
                 opening_hours=hours, est_cost_per_person=cost, **kw)


def prefs(**over) -> Preferences:
    base = dict(city="Bangalore", budget=2000, group_size=2, available_duration_minutes=240, timing_mode="exact",
                start_time=dt.time(16), end_time=dt.time(20), date=dt.date(2026, 11, 28))
    return Preferences(**{**base, **over})


def at(h, m=0, day=28) -> dt.datetime:
    return dt.datetime(2026, 11, day, h, m, tzinfo=IST)


# ---- availability -------------------------------------------------------

def test_opening_hours_open_closed_unverified():
    assert get_venue_availability(venue(hours="Mo-Su 10:00-22:00"), at(16), at(17)).status == "open"
    assert get_venue_availability(venue(hours="Mo-Su 10:00-22:00"), at(21), at(23)).status == "closed"
    assert get_venue_availability(venue(hours="Mo-Fr 09:00-18:00"), at(16), at(17)).status == "closed"  # Saturday
    assert get_venue_availability(venue(hours="sunrise-sunset"), at(16), at(17)).status == "unverified"
    assert get_venue_availability(venue(hours=None), at(16), at(17)).status == "unverified"


def test_overnight_hours_span_midnight():
    v = venue(hours="Fr 18:00-02:00")  # 2026-11-28 is a Saturday: open until 02:00 from Friday
    assert get_venue_availability(v, at(0, 30), at(1, 30)).status == "open"
    assert parse_opening_hours("24/7") is not None


# ---- temporal -----------------------------------------------------------

def test_exact_window_is_hard_and_defaults_to_saturday():
    t = resolve_temporal_context(prefs(), BLR, now=dt.datetime(2026, 11, 26, tzinfo=IST))
    assert t.hard_window and (t.earliest_start, t.latest_end) == (at(16), at(20))
    assert candidate_starts(t) == [at(16)]
    t2 = resolve_temporal_context(prefs(date=None), BLR, now=dt.datetime(2026, 11, 26, tzinfo=IST))
    assert t2.date == dt.date(2026, 11, 28) and t2.date_assumed


def test_tentative_allows_shift_within_flexibility():
    t = resolve_temporal_context(prefs(timing_mode="tentative", flexibility_minutes=30), BLR)
    starts = candidate_starts(t)
    assert min(starts) == at(15, 30) and max(starts) == at(16, 30) and at(16) in starts


def test_period_window():
    t = resolve_temporal_context(prefs(timing_mode="period", preferred_period="evening", start_time=None, end_time=None), BLR)
    assert (t.window_start, t.window_end) == (at(17), at(22))


# ---- parser -------------------------------------------------------------

def test_constraints_hard_vs_soft():
    cs = {c.code: c.kind for c in classify_constraints(["vegetarian", "avoid crowded places", "within 3 km", "dog friendly"])}
    assert cs == {"vegetarian": "hard", "avoid_crowds": "soft", "max_distance": "hard", "other": "soft"}


def test_missing_city_and_budget_asks_at_most_two_questions():
    r = parse_user_preferences({"mood": "happy"})
    assert r.preferences is None and set(r.missing) >= {"city", "budget"} and len(r.questions) <= 2


def test_contradictory_times_ask_for_clarification():
    r = parse_user_preferences({"city": "Pune", "budget": 1000, "timing_mode": "exact",
                                "start_time": "20:00", "end_time": "16:00"})
    assert r.preferences is None and "end time" in r.questions[0].lower()


def test_free_text_fallback_without_llm():
    r = parse_user_preferences({}, "Plan a quiet evening in Pune, budget ₹1,500 for 3 people, 3 hours, vegetarian, music",
                               message_overrides=True)
    p = r.preferences
    assert p and p.city == "Pune" and p.budget == 1500 and p.group_size == 3
    assert p.available_duration_minutes == 180 and "music" in p.interests and p.has_constraint("vegetarian")
    assert r.llm and not r.llm.used and r.llm.fallback_reason == "not_configured"


def test_budget_defaults_are_strict_and_disclosed():
    r = parse_user_preferences({"city": "Pune", "budget": 1000})
    assert r.preferences.budget_flexibility == "no" and any("strict" in a for a in r.assumptions)


# ---- cost & validation --------------------------------------------------

def _plan(p: Preferences, cost_pp: float):
    t = resolve_temporal_context(p, BLR)
    v = venue(cost=cost_pp)
    it = build_itinerary([v], at(16), Point(name="s", lat=12.97, lon=77.64), t, p, estimate_route, "balanced")
    return it, estimate_cost(it, p), t


def test_cost_includes_activity_and_buffer_and_per_person():
    p = prefs()
    _, cost, _ = _plan(p, 400)
    assert cost.total == 800 + round(800 * 0.05) and cost.per_person == cost.total / 2 and cost.within_budget


def test_budget_flexibility_rules():
    # 2 people * 1000 = 2000 + 5% buffer = 2100 -> over budget by 5%, within the 10% ceiling
    for flex, expect_pass, needs_approval in [("no", False, False), ("ask", True, True), ("yes", True, False)]:
        p = prefs(budget_flexibility=flex)
        it, cost, t = _plan(p, 1000)
        v = validate_plan(it, cost, p, t, None)
        assert (v.passed, v.requires_budget_approval) == (expect_pass, needs_approval), flex
    p = prefs(budget_flexibility="ask")
    it, cost, t = _plan(p, 1000)
    assert not validate_plan(it, cost, p, t, None, accept_extra_budget=True).requires_budget_approval


def test_beyond_ceiling_fails_even_with_flexibility():
    p = prefs(budget_flexibility="yes")
    it, cost, t = _plan(p, 1300)
    assert not validate_plan(it, cost, p, t, None).passed


def test_closed_venue_fails_validation():
    p = prefs()
    t = resolve_temporal_context(p, BLR)
    it = build_itinerary([venue(hours="Mo-Su 06:00-12:00")], at(16), Point(name="s", lat=12.97, lon=77.64), t, p,
                         estimate_route, "balanced")
    v = validate_plan(it, estimate_cost(it, p), p, t, None)
    assert not v.passed and any(c.name == "opening_hours" and c.status == "fail" for c in v.checks)


def test_builder_never_schedules_past_exact_window():
    p = prefs(available_duration_minutes=120, end_time=dt.time(18))
    t = resolve_temporal_context(p, BLR)
    vs = [venue(f"v{i}", cats=["entertainment.cinema"], lat=12.97 + i * 0.01) for i in range(3)]
    it = build_itinerary(vs, at(16), Point(name="s", lat=12.97, lon=77.64), t, p, estimate_route, "balanced")
    assert it.stops and it.ends_at <= at(18) and it.notes
