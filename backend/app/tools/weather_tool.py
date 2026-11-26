"""Tool 3: getWeatherReport (Open-Meteo hourly forecast for the outing window)."""
import datetime as dt
import logging
from typing import Optional
from zoneinfo import ZoneInfo

from ..config import get_assumptions
from ..schemas.context import HourWeather, Location, TemporalContext, WeatherReport
from ..services.geo import ProviderError, fetch_forecast

log = logging.getLogger(__name__)
_MAX_DAYS_AHEAD = 15  # Open-Meteo forecast horizon is ~16 days
_RAIN_CODES = set(range(51, 68)) | set(range(80, 83)) | {95, 96, 99}


def describe_code(code: Optional[int]) -> str:
    if code is None:
        return "unknown"
    if code == 0:
        return "clear"
    if code in (1, 2, 3):
        return "partly cloudy"
    if code in (45, 48):
        return "foggy"
    if code in _RAIN_CODES:
        return "thunderstorms" if code >= 95 else "rain"
    if 71 <= code <= 77:
        return "snow"
    return "mixed"


def _unavailable(reason: str, temporal: TemporalContext) -> WeatherReport:
    return WeatherReport(
        available=False, forecast_for=temporal.date.isoformat(),
        summary="Weather unavailable",
        warnings=[f"{reason}. Using conservative choices: outdoor stops are scored lower and indoor options preferred."],
    )


def get_weather_report(
    location: Location, temporal: TemporalContext, today: Optional[dt.date] = None
) -> WeatherReport:
    tz = ZoneInfo(temporal.timezone)
    today = today or dt.datetime.now(tz).date()
    ahead = (temporal.date - today).days
    if ahead < 0:
        return _unavailable("The outing date is in the past", temporal)
    if ahead > _MAX_DAYS_AHEAD:
        return _unavailable(f"No forecast yet for {temporal.date} (more than {_MAX_DAYS_AHEAD} days away)", temporal)
    try:
        data = fetch_forecast(location.lat, location.lon, temporal.date, temporal.timezone)
        h = data["hourly"]
        hours = [
            HourWeather(
                time=dt.datetime.fromisoformat(t).replace(tzinfo=tz),
                temp_c=h["temperature_2m"][i], precip_prob=h["precipitation_probability"][i],
                precip_mm=h["precipitation"][i], code=h["weathercode"][i], wind_kmh=h["windspeed_10m"][i],
            )
            for i, t in enumerate(h["time"])
        ]
    except (ProviderError, KeyError, IndexError, ValueError) as e:
        log.warning("weather unavailable: %s", e)
        return _unavailable("Weather service unavailable", temporal)

    lo = temporal.earliest_start.replace(minute=0, second=0, microsecond=0)
    window = [x for x in hours if lo <= x.time < temporal.latest_end]
    if not window:
        return _unavailable("Forecast did not cover the outing window", temporal)

    thr = get_assumptions()["weather"]
    temps = [x.temp_c for x in window if x.temp_c is not None]
    probs = [x.precip_prob for x in window if x.precip_prob is not None]
    max_prob = max(probs) if probs else None
    avg_t = round(sum(temps) / len(temps), 1) if temps else None
    rainy = (max_prob or 0) >= thr["rain_prob_pct"] or any(x.code in _RAIN_CODES for x in window)
    hot = bool(temps) and max(temps) >= thr["hot_c"]
    mid = window[len(window) // 2]
    summary = f"{describe_code(mid.code)}, ~{avg_t}°C" + (f", up to {max_prob}% rain chance" if max_prob is not None else "")
    return WeatherReport(
        available=True, fetched_at=dt.datetime.now(tz), forecast_for=temporal.date.isoformat(),
        hourly=hours, summary=summary, avg_temp_c=avg_t, max_precip_prob=max_prob,
        is_rainy=rainy, is_hot=hot,
    )
