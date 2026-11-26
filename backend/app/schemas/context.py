import datetime as dt
from typing import Literal, Optional

from pydantic import BaseModel


class Location(BaseModel):
    name: str
    lat: float
    lon: float
    timezone: str
    country: Optional[str] = None


class Point(BaseModel):
    name: str
    lat: float
    lon: float


class TemporalContext(BaseModel):
    city: str
    date: dt.date
    date_assumed: bool
    timezone: str
    timing_mode: Literal["exact", "tentative", "period"]
    window_start: dt.datetime  # nominal window (aware, local tz)
    window_end: dt.datetime
    earliest_start: dt.datetime  # earliest the plan may begin
    latest_end: dt.datetime  # latest the plan may end
    flexibility_minutes: int
    duration_minutes: int
    hard_window: bool  # exact timing: never schedule outside the window
    notes: list[str] = []


class HourWeather(BaseModel):
    time: dt.datetime
    temp_c: Optional[float] = None
    precip_prob: Optional[int] = None
    precip_mm: Optional[float] = None
    code: Optional[int] = None
    wind_kmh: Optional[float] = None


class WeatherReport(BaseModel):
    available: bool
    source: str = "Open-Meteo"
    attribution: str = "Weather data by Open-Meteo.com"
    fetched_at: Optional[dt.datetime] = None
    forecast_for: str = ""
    hourly: list[HourWeather] = []
    summary: str = ""
    avg_temp_c: Optional[float] = None
    max_precip_prob: Optional[int] = None
    is_rainy: bool = False
    is_hot: bool = False
    warnings: list[str] = []

    def hour_at(self, t: dt.datetime) -> Optional[HourWeather]:
        key = t.replace(minute=0, second=0, microsecond=0)
        return next((h for h in self.hourly if h.time == key), None)


class RouteEstimate(BaseModel):
    origin: str
    destination: str
    distance_km: float
    duration_min: float
    delay_min: float = 0.0
    traffic_condition: Literal["light", "moderate", "heavy", "unknown"] = "unknown"
    departure_time: dt.datetime
    mode: str
    source: Literal["google_routes", "estimate"]
    is_live_traffic: bool = False
    note: Optional[str] = None


class CrowdEstimate(BaseModel):
    level: Literal["low", "moderate", "high", "unknown"]
    confidence: Literal["low", "medium", "high"] = "low"
    evidence: str = ""
    is_heuristic: bool = True
    score: Optional[float] = None


class Availability(BaseModel):
    status: Literal["open", "closed", "unverified"]
    hours_text: Optional[str] = None
    source: str = "geoapify/openstreetmap"
    note: Optional[str] = None
