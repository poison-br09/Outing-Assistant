"""Tool 2: resolveTemporalContext. One shared, timezone-aware time frame for all later tools."""
import datetime as dt
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..config import get_assumptions
from ..schemas.context import Location, TemporalContext
from ..schemas.preferences import Preferences


def resolve_temporal_context(
    prefs: Preferences, location: Location, now: Optional[dt.datetime] = None
) -> TemporalContext:
    a, notes = get_assumptions(), []
    try:
        tz = ZoneInfo(location.timezone)
    except ZoneInfoNotFoundError:
        tz = ZoneInfo("UTC")
        notes.append(f"Unknown timezone '{location.timezone}': using UTC.")
    today = (now or dt.datetime.now(tz)).astimezone(tz).date()

    date, assumed = prefs.date, False
    if date is None:
        date = today + dt.timedelta(days=(5 - today.weekday()) % 7)
        assumed = True
        notes.append(f"No date given: assumed the upcoming Saturday ({date.isoformat()}).")

    midnight = dt.datetime.combine(date, dt.time(0), tzinfo=tz)

    def at_clock(t: dt.time) -> dt.datetime:
        return midnight + dt.timedelta(hours=t.hour, minutes=t.minute)

    flex = 0
    if prefs.timing_mode == "period":
        lo, hi = a["periods"][prefs.preferred_period]
        ws, we = midnight + dt.timedelta(hours=lo), midnight + dt.timedelta(hours=hi)
        earliest, latest = ws, we
    else:
        ws, we = at_clock(prefs.start_time), at_clock(prefs.end_time)
        if we <= ws:
            we += dt.timedelta(days=1)
        if prefs.timing_mode == "tentative":
            flex = prefs.flexibility_minutes
        earliest, latest = ws - dt.timedelta(minutes=flex), we + dt.timedelta(minutes=flex)

    span = int((latest - earliest).total_seconds() // 60)
    duration = min(prefs.available_duration_minutes, span)
    if duration < prefs.available_duration_minutes:
        notes.append(f"Plan length limited to {duration} min by the allowed time window.")

    return TemporalContext(
        city=prefs.city, date=date, date_assumed=assumed, timezone=str(tz),
        timing_mode=prefs.timing_mode, window_start=ws, window_end=we,
        earliest_start=earliest, latest_end=latest, flexibility_minutes=flex,
        duration_minutes=duration, hard_window=prefs.timing_mode == "exact", notes=notes,
    )


def candidate_starts(t: TemporalContext, step_min: int = 30) -> list[dt.datetime]:
    """Allowed plan start times: exact = one; otherwise every `step_min` within the window."""
    if t.hard_window:
        return [t.window_start]
    last = t.latest_end - dt.timedelta(minutes=t.duration_minutes)
    if last <= t.earliest_start:
        return [t.earliest_start]
    out, cur = [], t.earliest_start
    while cur <= last:
        out.append(cur)
        cur += dt.timedelta(minutes=step_min)
    if t.timing_mode == "tentative" and t.window_start not in out:
        out.append(t.window_start)
    return sorted(out)
