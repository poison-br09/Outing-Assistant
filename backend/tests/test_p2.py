"""Map links, hours verification, calendar export and multi-day trips (stubbed providers)."""
import datetime as dt
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

import app.tools.venue_verifier as vv
from app.agent.orchestrator import Orchestrator
from app.main import app
from app.schemas.api import FinalizeRequest, PlanRequest, TripRequest
from app.services.geo import ProviderError
from app.tools.calendar_export import _fold, build_ics, google_calendar_link
from app.tools.venue_availability import get_venue_availability, parse_opening_hours
from app.tools.venue_verifier import verify_venues

from .conftest import form
from .test_tools import at, venue

agent = Orchestrator()


def recommended(r):
    return next(p for p in r.proposals if p.id == r.recommended_id)


# ---- map / navigation ---------------------------------------------------

def test_route_url_has_origin_waypoints_and_destination(stubbed, future_date):
    r = agent.run(PlanRequest(fields=form(future_date, budget_flexibility="no")))
    it = recommended(r).itinerary
    q = parse_qs(urlparse(it.route_url).query)
    assert q["origin"] == [f"{it.origin.lat},{it.origin.lon}"]
    assert q["destination"] == [f"{it.stops[-1].venue.lat},{it.stops[-1].venue.lon}"]
    assert ("waypoints" in q) == (len(it.stops) > 1)
    assert "route_url" in it.model_dump(mode="json")


# ---- hours verification --------------------------------------------------

def test_public_holiday_rules_are_ignored_not_unverifiable():
    assert parse_opening_hours("Mo-Su 10:00-22:00; PH off") is not None
    v = venue(hours="Mo-Su,PH 10:00-22:00")
    a = get_venue_availability(v, at(16), at(17))
    assert a.status == "open" and "Public-holiday" in a.note


def test_verifier_fills_missing_hours_and_counts_failures(monkeypatch):
    monkeypatch.setenv("GEOAPIFY_API_KEY", "x")
    from app.config import get_settings
    get_settings.cache_clear()
    try:
        a, b, c = venue("a", hours=None), venue("b", hours=None), venue("c", hours="Mo-Su 10:00-20:00")

        def fake(pid):
            if pid == "b":
                raise ProviderError("geoapify", "HTTP 500")
            return {"opening_hours": "Mo-Su 09:00-21:00", "website": "https://example.test"}
        monkeypatch.setattr(vv, "fetch_place_details", fake)
        rep = verify_venues([a, b, c])
        assert (rep.attempted, rep.enriched, rep.failed) == (2, 1, 1)
        assert a.opening_hours and a.hours_source == "place_details" and a.website
        assert b.opening_hours is None and c.hours_source == "none"  # untouched
        assert get_venue_availability(a, at(16), at(17)).source == "Geoapify place details"
    finally:
        get_settings.cache_clear()


def test_verifier_skips_without_key():
    rep = verify_venues([venue(hours=None)])
    assert rep.attempted == 0 and rep.skipped_reason


# ---- calendar ------------------------------------------------------------

def test_ics_is_valid_escaped_and_folded(stubbed, future_date):
    r = agent.run(PlanRequest(fields=form(future_date, budget_flexibility="no")))
    p = recommended(r)
    p.itinerary.stops[0].why = "Fun; with commas, semicolons and a very long explanation " * 4
    ics = build_ics(p, "Bangalore")
    assert ics.startswith("BEGIN:VCALENDAR\r\n") and ics.endswith("END:VCALENDAR\r\n")
    assert ics.count("BEGIN:VEVENT") == len(p.itinerary.stops) == ics.count("END:VEVENT")
    assert ics.count("BEGIN:VALARM") == 1 and "DTSTART:" in ics
    assert all(len(line.encode()) <= 75 for line in ics.split("\r\n"))
    bs = chr(92)
    assert bs + ";" in ics and bs + "," in ics
    link = google_calendar_link(p, "Bangalore")
    assert link.startswith("https://calendar.google.com/calendar/render?") and "dates=" in link


def test_fold_never_splits_multibyte_characters():
    line = "SUMMARY:" + "₹" * 60
    folded = _fold(line)
    assert all(len(x.encode()) <= 75 for x in folded)
    assert "".join(x[1:] if i else x for i, x in enumerate(folded)) == line


def test_final_plan_carries_calendar_and_route(stubbed, future_date):
    r = agent.run(PlanRequest(fields=form(future_date, budget_flexibility="no")))
    f = agent.finalize(FinalizeRequest(preferences=r.preferences, proposal=recommended(r), temporal=r.temporal,
                                       weather=r.weather, approved=True))
    assert f.ics.startswith("BEGIN:VCALENDAR") and f.calendar_link and f.route_link


# ---- multi-day trips -----------------------------------------------------

def trip_req(date, days=2, fixed=0.0, **over):
    f = form(date, interests=["walks", "music", "culture"], budget=6000, budget_flexibility="no", **over)
    return TripRequest(plan=PlanRequest(fields=f), days=days, fixed_costs=fixed)


def test_trip_has_consecutive_dates_split_budget_and_no_repeated_venues(stubbed, future_date):
    t = agent.run_trip(trip_req(future_date, fixed=1000))
    assert [d.date for d in t.days] == [future_date, future_date + dt.timedelta(days=1)]
    assert all(d.response.preferences.budget == 2500 for d in t.days)  # (6000 - 1000) / 2
    ids = [{s.venue.id for s in recommended(d.response).itinerary.stops}
           for d in t.days if d.response.status == "proposal"]
    assert ids and (len(ids) < 2 or not (ids[0] & ids[1]))
    assert t.status == "proposal" and t.fixed_costs == 1000
    assert any("Lodging and intercity travel" in w for w in t.warnings)


def test_trip_total_includes_fixed_costs(stubbed, future_date):
    t = agent.run_trip(trip_req(future_date, fixed=500))
    planned = sum(recommended(d.response).cost.total for d in t.days if d.response.status == "proposal")
    assert t.estimated_total == round(planned + 500)


def test_trip_fixed_costs_exceeding_budget_asks_question(stubbed, future_date):
    t = agent.run_trip(trip_req(future_date, fixed=9000))
    assert t.status == "needs_clarification" and t.questions


def test_trip_missing_info_and_unknown_city(stubbed, future_date):
    f = form(future_date)
    f.pop("budget")
    assert agent.run_trip(TripRequest(plan=PlanRequest(fields=f), days=2)).status == "needs_clarification"
    f = form(future_date, city="Atlantis")
    f.pop("date")
    assert agent.run_trip(TripRequest(plan=PlanRequest(fields=f), days=2)).status == "needs_clarification"


def test_trip_without_venue_provider_degrades_per_day(no_venue_provider, future_date):
    t = agent.run_trip(trip_req(future_date))
    assert t.status == "fallback" and all(d.response.generic_suggestions for d in t.days)


def test_trip_api_and_validation(stubbed, future_date):
    c = TestClient(app)
    body = trip_req(future_date).model_dump(mode="json")
    r = c.post("/api/v1/trip", json=body)
    assert r.status_code == 200 and len(r.json()["days"]) == 2
    assert c.post("/api/v1/trip", json={**body, "days": 1}).status_code == 422  # trips are 2-5 days
