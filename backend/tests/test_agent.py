"""End-to-end orchestrator scenarios against stubbed providers (no network, no LLM)."""
import datetime as dt
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from app.agent.orchestrator import Orchestrator
from app.main import app
from app.schemas.api import FinalizeRequest, PlanRequest
from app.services.geo import ProviderError

from .conftest import form

agent = Orchestrator()


def plan(date, message=None, excluded=None, **over):
    return agent.run(PlanRequest(fields=form(date, **over), message=message, excluded_venue_ids=excluded or []))


def test_normal_outing_produces_plans_and_trace(stubbed, future_date):
    r = plan(future_date)
    assert r.status == "proposal" and r.proposals and r.recommended_id
    names = [s.name for s in r.trace]
    for expected in ["Preferences parsed", "Temporal context resolved", "Weather checked", "Activities discovered",
                     "Activities ranked", "Traffic checked", "Crowd context estimated", "Food options retrieved",
                     "Itinerary optimised", "Plans validated", "Awaiting user approval"]:
        assert expected in names
    assert all(s.status in ("success", "warning") for s in r.trace)
    # no LLM configured: disclosed in the trace and in the LLM info
    assert any(i.fallback_reason for i in r.llm) or not r.llm
    assert r.weather and r.weather.available


def test_exact_timing_never_scheduled_outside_window(stubbed, future_date):
    r = plan(future_date)
    for p in r.proposals:
        assert p.itinerary.departs_at.hour == 16 and p.itinerary.ends_at <= p.itinerary.departs_at.replace(hour=20, minute=0)


def test_vegetarian_meal_is_verified_and_budget_respected(stubbed, future_date):
    r = plan(future_date, budget_flexibility="no")
    for p in r.proposals:
        assert p.cost.total <= p.cost.budget
        meals = [s for s in p.itinerary.stops if s.venue.kind == "food"]
        assert all(m.venue.diet_verified for m in meals)


def test_stops_are_ordered_in_time_with_travel_gaps(stubbed, future_date):
    r = plan(future_date)
    stops = r.proposals[0].itinerary.stops
    for a, b in zip(stops, stops[1:]):
        assert b.start >= a.end


def test_rain_prefers_indoor(stubbed, future_date):
    import app.tools.weather_tool as wt

    def rainy(lat, lon, date, tz):
        f = __import__("tests.conftest", fromlist=["x"]).fake_forecast(lat, lon, date, tz)
        f["hourly"]["precipitation_probability"] = [90] * 48
        f["hourly"]["weathercode"] = [63] * 48
        return f
    stubbed.setattr(wt, "fetch_forecast", rainy)
    r = plan(future_date)
    assert r.weather.is_rainy
    stops = [s for p in r.proposals for s in p.itinerary.stops if s.venue.kind == "activity"]
    assert stops and sum(s.venue.indoor for s in stops) >= sum(not s.venue.indoor for s in stops)


def test_weather_outage_degrades_with_warning(stubbed, future_date):
    import app.tools.weather_tool as wt

    def boom(*a, **k):
        raise ProviderError("open-meteo", "timeout")
    stubbed.setattr(wt, "fetch_forecast", boom)
    r = plan(future_date)
    assert r.status == "proposal" and not r.weather.available
    assert any("conservative" in w for w in r.warnings)
    assert next(s for s in r.trace if s.name == "Weather checked").status == "warning"


def test_venue_provider_outage_returns_generic_fallback(no_venue_provider, future_date):
    r = plan(future_date)
    assert r.status == "fallback" and r.generic_suggestions
    assert all("Generic" in s for s in r.generic_suggestions) and not r.proposals
    assert "unavailable" in r.message.lower()


def test_unsupported_city_asks_for_another(stubbed, future_date):
    r = plan(future_date, city="Atlantis")
    assert r.status == "needs_clarification" and "Atlantis" in r.message


def test_missing_budget_asks_question(stubbed, future_date):
    f = form(future_date)
    f.pop("budget")
    r = agent.run(PlanRequest(fields=f))
    assert r.status == "needs_clarification" and any("budget" in q.lower() for q in r.questions)


def test_tiny_budget_degrades_gracefully(stubbed, future_date):
    r = plan(future_date, budget=40, budget_flexibility="no")
    assert r.status in ("proposal", "fallback")
    if r.status == "proposal":
        assert all(p.cost.total <= p.cost.budget for p in r.proposals)
    else:
        assert r.generic_suggestions


def test_very_short_window_is_a_single_stop(stubbed, future_date):
    r = plan(future_date, end_time="17:00", available_duration_minutes=60)
    assert r.status == "proposal"
    assert all(len(p.itinerary.stops) == 1 for p in r.proposals)


def test_excluded_venue_is_not_proposed(stubbed, future_date):
    first = plan(future_date)
    banned = first.proposals[0].itinerary.stops[0].venue.id
    again = plan(future_date, excluded=[banned])
    ids = {s.venue.id for p in again.proposals for s in p.itinerary.stops}
    assert banned not in ids


def test_past_date_asks_for_clarification(stubbed):
    r = plan(dt.date.today() - dt.timedelta(days=2))
    assert r.status == "needs_clarification"


# ---- approval workflow --------------------------------------------------

def _finalize_req(r, approved, accept=False):
    p = next(x for x in r.proposals if x.id == r.recommended_id)
    return FinalizeRequest(preferences=r.preferences, proposal=p, temporal=r.temporal, weather=r.weather,
                           approved=approved, accept_extra_budget=accept)


def test_cannot_finalise_without_approval(stubbed, future_date):
    r = plan(future_date)
    with pytest.raises(PermissionError):
        agent.finalize(_finalize_req(r, approved=False))


def test_finalise_after_approval(stubbed, future_date):
    r = plan(future_date, budget_flexibility="no")
    final = agent.finalize(_finalize_req(r, approved=True))
    assert final.timeline and final.caveats and any("No booking" in c for c in final.caveats)
    assert [s.name for s in final.trace] == ["Approval received", "Plan re-validated", "Final plan generated"]
    assert all(t.map_link for t in final.timeline)


def test_over_budget_plan_needs_explicit_acceptance(stubbed, future_date):
    r = plan(future_date, budget_flexibility="ask")
    over = [p for p in r.proposals if p.requires_budget_approval]
    for p in over:
        req = FinalizeRequest(preferences=r.preferences, proposal=p, temporal=r.temporal, weather=r.weather, approved=True)
        with pytest.raises(ValueError):
            agent.finalize(req)
        assert agent.finalize(req.model_copy(update={"accept_extra_budget": True})).timeline


# ---- API ----------------------------------------------------------------

def test_api_plan_and_finalize_roundtrip(stubbed, future_date):
    c = TestClient(app)
    assert c.get("/health").json()["status"] == "ok"
    body = c.post("/api/v1/plan", json={"fields": form(future_date, budget_flexibility="no")}).json()
    assert body["status"] == "proposal"
    prop = next(p for p in body["proposals"] if p["id"] == body["recommended_id"])
    payload = {"preferences": body["preferences"], "proposal": prop, "temporal": body["temporal"],
               "weather": body["weather"], "approved": False}
    assert c.post("/api/v1/finalize", json=payload).status_code == 403
    assert c.post("/api/v1/finalize", json={**payload, "approved": True}).status_code == 200
    assert "calls" in c.get("/metrics").json()["llm"]
