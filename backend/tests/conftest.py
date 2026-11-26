"""Test doubles for the provider layer. Fake venues exist ONLY here; the product ships no catalogue."""
import datetime as dt

import pytest

from app.schemas.context import Location
from app.services.geo import ProviderError

BLR = Location(name="Bangalore", lat=12.9716, lon=77.5946, timezone="Asia/Kolkata", country="India")


def _place(i: int, name: str, cats: list[str], hours="Mo-Su 06:00-23:00", **extra) -> dict:
    return {"place_id": f"p{i}", "name": name, "lat": 12.9719 + i * 0.002, "lon": 77.6412 + i * 0.002,
            "categories": cats, "formatted": f"{name}, Test City", "opening_hours": hours, **extra}


ACTIVITIES = {
    "leisure.park": [_place(1, "Test Park", ["leisure.park"]), _place(2, "Test Garden", ["leisure.park.garden"])],
    "tourism.attraction": [_place(3, "Test Landmark", ["tourism.attraction"])],
    "entertainment.culture": [_place(4, "Test Arts Centre", ["entertainment.culture.arts_centre"])],
    "catering.pub": [_place(5, "Test Pub", ["catering.pub"])],
    "entertainment.cinema": [_place(6, "Test Cinema", ["entertainment.cinema"])],
}
FOODS = [_place(10, "Test Veg Kitchen", ["catering.restaurant"]), _place(11, "Test Cafe", ["catering.cafe"]),
         _place(12, "Test Diner", ["catering.restaurant"])]


def fake_search_places(categories, lat, lon, radius_m, limit, conditions=None):
    if "catering.restaurant" in categories:
        return FOODS[:1] if conditions else FOODS
    out = []
    for c in categories:
        out += ACTIVITIES.get(c, [])
    return out


def fake_forecast(lat, lon, date, timezone):
    times, n = [], 48
    for i in range(n):
        d = date + dt.timedelta(days=i // 24)
        times.append(f"{d.isoformat()}T{i % 24:02d}:00")
    return {"hourly": {
        "time": times, "temperature_2m": [26.0] * n, "precipitation_probability": [10] * n,
        "precipitation": [0.0] * n, "weathercode": [1] * n, "windspeed_10m": [8.0] * n,
    }}


@pytest.fixture
def future_date() -> dt.date:
    return dt.date.today() + dt.timedelta(days=3)


@pytest.fixture
def stubbed(monkeypatch):
    import app.agent.orchestrator as orch
    import app.tools.activity_finder as af
    import app.tools.food_finder as ff
    import app.tools.weather_tool as wt
    monkeypatch.setattr(orch, "resolve_city", lambda name: BLR if name.lower() == "bangalore" else None)
    monkeypatch.setattr(orch, "geocode_place", lambda text, near=None: (12.9719, 77.6412))
    monkeypatch.setattr(af, "search_places", fake_search_places)
    monkeypatch.setattr(ff, "search_places", fake_search_places)
    monkeypatch.setattr(wt, "fetch_forecast", fake_forecast)
    return monkeypatch


@pytest.fixture
def no_venue_provider(stubbed):
    import app.tools.activity_finder as af
    import app.tools.food_finder as ff

    def boom(*a, **k):
        raise ProviderError("geoapify", "API key not configured")
    stubbed.setattr(af, "search_places", boom)
    stubbed.setattr(ff, "search_places", boom)


def form(date: dt.date, **over) -> dict:
    base = {
        "city": "Bangalore", "starting_location": "Indiranagar", "group_type": "friends", "group_size": 2,
        "budget": 2000, "budget_type": "total", "budget_flexibility": "ask", "date": date.isoformat(),
        "timing_mode": "exact", "start_time": "16:00", "end_time": "20:00", "available_duration_minutes": 240,
        "mood": "tired but wants to do something fun", "interests": ["walks", "music", "food"],
        "constraints": ["vegetarian", "avoid crowded places"],
    }
    return {**base, **over}
