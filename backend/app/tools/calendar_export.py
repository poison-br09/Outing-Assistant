"""Calendar export: an RFC 5545 .ics file and a Google Calendar link for an approved plan.

Times are written in UTC so every calendar app places them correctly without VTIMEZONE blocks.
"""
import datetime as dt
import uuid
from urllib.parse import urlencode

from ..schemas.itinerary import PlanProposal

_FMT = "%Y%m%dT%H%M%SZ"


def _utc(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).strftime(_FMT)


def _esc(text: str) -> str:
    bs = "\\"
    return (text.replace(bs, bs + bs).replace(";", bs + ";").replace(",", bs + ",")
            .replace("\r\n", bs + "n").replace("\n", bs + "n"))


def _fold(line: str) -> list[str]:
    """Fold to <=75 octets per line, never splitting a UTF-8 character."""
    b, out, limit = line.encode("utf-8"), [], 75
    while len(b) > limit:
        cut = limit
        while (b[cut] & 0xC0) == 0x80:
            cut -= 1
        out.append(b[:cut].decode("utf-8"))
        b, limit = b[cut:], 74  # continuation lines start with one space
    out.append(b.decode("utf-8"))
    return [out[0], *(" " + x for x in out[1:])]


def build_ics(proposal: PlanProposal, city: str, now: dt.datetime | None = None) -> str:
    stamp = _utc(now or dt.datetime.now(dt.timezone.utc))
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Outing Assistant//EN", "CALSCALE:GREGORIAN", "METHOD:PUBLISH"]
    for i, s in enumerate(proposal.itinerary.stops):
        v = s.venue
        desc = [s.why] if s.why else []
        desc.append(f"Directions: https://www.google.com/maps/dir/?api=1&destination={v.lat},{v.lon}")
        desc.append("Plan only: nothing is booked. Hours and prices are unverified.")
        uid = uuid.uuid5(uuid.NAMESPACE_URL, f"{proposal.id}|{v.id}|{s.start.isoformat()}")
        ev = ["BEGIN:VEVENT", f"UID:{uid}@outing-assistant", f"DTSTAMP:{stamp}",
              f"DTSTART:{_utc(s.start)}", f"DTEND:{_utc(s.end)}",
              f"SUMMARY:{_esc(v.name)}", f"LOCATION:{_esc(v.address or f'{v.name}, {city}')}",
              f"DESCRIPTION:{_esc(chr(10).join(desc))}"]
        if i == 0:
            ev += ["BEGIN:VALARM", "TRIGGER:-PT30M", "ACTION:DISPLAY", "DESCRIPTION:Your outing starts soon", "END:VALARM"]
        ev.append("END:VEVENT")
        lines += ev
    lines.append("END:VCALENDAR")
    return "\r\n".join(f for line in lines for f in _fold(line)) + "\r\n"


def google_calendar_link(proposal: PlanProposal, city: str) -> str:
    it = proposal.itinerary
    first = it.stops[0]
    details = "\n".join(f"{s.start:%H:%M}-{s.end:%H:%M} {s.venue.name}" for s in it.stops)
    return "https://calendar.google.com/calendar/render?" + urlencode({
        "action": "TEMPLATE", "text": f"Outing in {city}: {proposal.title}",
        "dates": f"{_utc(first.start)}/{_utc(it.ends_at)}",
        "details": details + "\n\nPlan only: nothing is booked.",
        "location": first.venue.address or f"{first.venue.name}, {city}",
    })
