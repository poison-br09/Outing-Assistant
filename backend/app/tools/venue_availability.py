"""Tool 6: getVenueAvailability. Parses OSM-style opening_hours from the place provider.

Anything we cannot parse or that is missing is reported as 'unverified', never as open.
"""
import datetime as dt
import re
from functools import lru_cache
from typing import Optional

from ..schemas.context import Availability
from ..schemas.itinerary import Venue

_DAYS = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]
_DAY = "(?:Mo|Tu|We|Th|Fr|Sa|Su)"
_RULE = re.compile(rf"^({_DAY}(?:[-,]{_DAY})*)?\s*(.*)$")
_RANGE = re.compile(r"(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})")

Schedule = dict[int, list[tuple[int, int]]]  # weekday -> [(start_min, end_min)] (end may exceed 1440)


def _days(spec: Optional[str]) -> list[int]:
    if not spec:
        return list(range(7))
    out: list[int] = []
    for part in spec.split(","):
        if "-" in part:
            a, b = (_DAYS.index(x) for x in part.split("-"))
            out += [(a + i) % 7 for i in range(((b - a) % 7) + 1)]
        else:
            out.append(_DAYS.index(part))
    return out


@lru_cache(maxsize=2048)
def parse_opening_hours(text: str) -> Optional[Schedule]:
    t = text.strip()
    if t == "24/7":
        return {d: [(0, 1440)] for d in range(7)}
    sched: Schedule = {d: [] for d in range(7)}
    for rule in filter(None, (r.strip() for r in t.split(";"))):
        m = _RULE.match(rule)
        if not m:
            return None
        days, rest = _days(m.group(1)), m.group(2).strip().lower()
        if rest in ("off", "closed"):
            for d in days:
                sched[d] = []
            continue
        ranges = []
        for r in rest.split(","):
            rm = _RANGE.fullmatch(r.strip())
            if not rm:
                return None  # sunrise/sunset, PH, week numbers... => unverified
            s, e = int(rm[1]) * 60 + int(rm[2]), int(rm[3]) * 60 + int(rm[4])
            ranges.append((s, e + 1440 if e <= s else e))
        for d in days:
            sched[d] = ranges
    return sched


def _open_during(sched: Schedule, start: dt.datetime, end: dt.datetime) -> bool:
    s = start.hour * 60 + start.minute
    e = s + int((end - start).total_seconds() // 60)
    wd = start.weekday()
    ranges = list(sched[wd]) + [(a - 1440, b - 1440) for a, b in sched[(wd - 1) % 7] if b > 1440]
    return any(a <= s and e <= b for a, b in ranges)


def get_venue_availability(venue: Venue, start: dt.datetime, end: dt.datetime) -> Availability:
    if not venue.opening_hours:
        return Availability(status="unverified", note="No opening hours in provider data; confirm before going.")
    sched = parse_opening_hours(venue.opening_hours)
    if sched is None:
        return Availability(
            status="unverified", hours_text=venue.opening_hours,
            note="Opening hours could not be interpreted; confirm before going.",
        )
    ok = _open_during(sched, start, end)
    return Availability(
        status="open" if ok else "closed", hours_text=venue.opening_hours,
        note=None if ok else "Provider hours indicate it is closed for the planned visit.",
    )
