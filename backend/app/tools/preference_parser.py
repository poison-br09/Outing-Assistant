"""Tool 1: parseUserPreferences.

Merges form fields with free text (LLM extraction, deterministic fallback), normalises,
classifies constraints as hard/soft, and asks at most two clarifying questions.
"""
import datetime as dt
import re
from typing import Any, Optional

from pydantic import BaseModel, ValidationError

from ..agent.prompts import PARSE_SYSTEM
from ..config import get_assumptions
from ..llm.client import try_llm
from ..schemas.api import LLMInfo
from ..schemas.preferences import Constraint, PartialPreferences, Preferences

# (code, regex, kind). A constraint string can match several rules.
_RULES = [
    ("vegan", r"vegan", "hard"),
    ("vegetarian", r"vegetarian|veggie|\bveg\b|pure veg", "hard"),
    ("allergy", r"allerg|gluten|lactose|\bnuts?\b", "hard"),
    ("diet_other", r"halal|kosher|jain", "hard"),
    ("alcohol_free", r"alcohol|teetotal|no drinks|\bdry\b", "hard"),
    ("accessibility", r"wheelchair|accessib|mobility|step-free", "hard"),
    ("family_friendly", r"family|\bkids?\b|child", "hard"),
    ("avoid_outdoor", r"(avoid|no|not|without|skip|don'?t).{0,12}outdoor|indoors? only|stay indoors", "hard"),
    ("avoid_long_walks", r"long walk|avoid walking|minimal walking|limited walking", "hard"),
    ("max_distance", r"(?:within|under|max\w*)\D{0,15}(\d+(?:\.\d+)?)\s*km", "hard"),
    ("avoid_crowds", r"crowd|quiet|peaceful|busy|noisy", "soft"),
]


class ParseResult(BaseModel):
    preferences: Optional[Preferences] = None
    partial: dict[str, Any] = {}
    missing: list[str] = []
    questions: list[str] = []
    assumptions: list[str] = []
    llm: Optional[LLMInfo] = None


def classify_constraints(texts: list[str]) -> list[Constraint]:
    out: list[Constraint] = []
    seen: set[tuple[str, str]] = set()
    for text in texts:
        t, matched = text.strip(), False
        for code, pattern, kind in _RULES:
            m = re.search(pattern, t, re.I)
            if m:
                matched = True
                value = float(m.group(1)) if code == "max_distance" else None
                if (code, t) not in seen:
                    seen.add((code, t))
                    out.append(Constraint(text=t, kind=kind, code=code, value=value))
        if t and not matched and ("other", t) not in seen:
            seen.add(("other", t))
            out.append(Constraint(text=t, kind="soft", code="other"))
    return out


def _clean(d: dict) -> dict:
    return {k: v for k, v in d.items() if v not in (None, "", [])}


def _validate_partial(raw: dict) -> tuple[PartialPreferences, list[str]]:
    """Drop fields that fail validation (reported so we can ask about them)."""
    invalid: list[str] = []
    for _ in range(3):
        try:
            return PartialPreferences.model_validate(raw), invalid
        except ValidationError as e:
            bad = {str(err["loc"][0]) for err in e.errors() if err["loc"]}
            if not bad:
                break
            invalid += sorted(bad)
            raw = {k: v for k, v in raw.items() if k not in bad}
    return PartialPreferences(), invalid


def _regex_extract(message: str) -> dict:
    """Deterministic fallback when the LLM is unavailable. Deliberately conservative."""
    a, m, out = get_assumptions(), message, {}
    low = m.lower()
    if b := re.search(r"(?:₹|rs\.?|inr)\s*([\d,]+)|([\d,]+)\s*(?:rupees|rs\b|inr)|budget\D{0,12}([\d,]{3,})", m, re.I):
        out["budget"] = float(next(g for g in b.groups() if g).replace(",", ""))
    if d := re.search(r"(\d+(?:\.\d+)?)\s*(hours?|hrs?|h\b|minutes?|mins?)", low):
        n = float(d.group(1))
        out["available_duration_minutes"] = int(n * 60 if d.group(2).startswith("h") else n)
    if g := re.search(r"(\d+)\s*(?:people|persons|of us|friends|adults)", low):
        out["group_size"] = int(g.group(1))
    for gt in ("solo", "couple", "family", "friends"):
        if gt in low:
            out["group_type"] = gt
            break
    out["interests"] = [i for i in a["interest_categories"] if re.search(rf"\b{i}\b", low)]
    out["constraints"] = [c for c in ("vegetarian", "vegan", "avoid crowded places", "alcohol-free", "wheelchair")
                          if (c.split()[0] in low and c != "avoid crowded places")
                          or (c == "avoid crowded places" and re.search(r"crowd", low))]
    if c := re.search(r"\bin ([A-Z][a-zA-Z]+(?: [A-Z][a-zA-Z]+)?)", m):
        out["city"] = c.group(1)
    for p in ("morning", "afternoon", "evening", "night"):
        if p in low:
            out["preferred_period"], out["timing_mode"] = p, "period"
            break
    out["mood"] = m.strip()
    return out


def _extract(message: str, today: dt.date) -> tuple[dict, LLMInfo]:
    data, info = try_llm(PARSE_SYSTEM.format(today=today.isoformat()), message, PartialPreferences)
    if data is not None:
        return data.model_dump(mode="json", exclude_none=True), info
    return _regex_extract(message), info


def _minutes(t: dt.time) -> int:
    return t.hour * 60 + t.minute


def _add(t: dt.time, minutes: int) -> dt.time:
    return (dt.datetime.combine(dt.date.today(), t) + dt.timedelta(minutes=minutes)).time()


def parse_user_preferences(
    fields: dict, message: Optional[str] = None, message_overrides: bool = False,
    today: Optional[dt.date] = None,
) -> ParseResult:
    a = get_assumptions()
    today = today or dt.date.today()
    merged, info = _clean(fields), None
    if message and message.strip():
        extracted, info = _extract(message, today)
        extracted = _clean(extracted)
        merged = {**merged, **extracted} if message_overrides else {**extracted, **merged}

    p, invalid = _validate_partial(merged)
    d = p.model_dump(exclude_none=True)
    partial = p.model_dump(mode="json", exclude_none=True)

    questions: list[str] = []
    missing: list[str] = []
    if invalid:
        questions.append(f"I couldn't understand these details: {', '.join(invalid)}. Could you rephrase them?")
    if not d.get("city"):
        missing.append("city")
        questions.append("Which city is the outing in?")
    if not d.get("budget"):
        missing.append("budget")
        questions.append("What is your budget for the outing (total, or per person)?")

    # ---- timing --------------------------------------------------------
    assumptions: list[str] = []
    flex = d.get("flexibility_minutes", 0)
    start, end = d.get("start_time"), d.get("end_time")
    duration = d.get("available_duration_minutes")
    mode, period = d.get("timing_mode"), d.get("preferred_period")
    if start and duration and not end:
        end = _add(start, duration)
    elif end and duration and not start:
        start = _add(end, -duration)
    if mode is None:
        if start and end:
            mode = "tentative" if flex > 0 else "exact"
        else:
            mode = "period"
    if mode == "period" and period is None:
        period = "any"
        if not (start and end):
            assumptions.append("No time of day given: planning flexibly within the day.")
    if mode in ("exact", "tentative") and not (start and end):
        missing.append("start_time/end_time")
        questions.append("What start and end time do you have in mind (or choose morning/afternoon/evening)?")
    elif start and end:
        span = _minutes(end) - _minutes(start)
        if span <= 0:
            questions.append("Your end time is not after your start time. What start and end time did you mean?")
        elif p.available_duration_minutes and mode == "exact" and abs(span - p.available_duration_minutes) > 15:
            questions.append(
                f"Your time window is {span} minutes but you said {p.available_duration_minutes} minutes are available. Which is right?"
            )
        duration = duration or (span if span > 0 else None)
    if duration is None:
        duration = a["default_duration_min"]
        assumptions.append(f"No duration given: assumed {duration // 60}h.")

    if questions:
        return ParseResult(partial=partial, missing=missing, questions=questions[:2], assumptions=assumptions, llm=info)

    # ---- group, budget, constraints -------------------------------------
    gtype = d.get("group_type", "mixed")
    size = d.get("group_size")
    if size is None:
        size = {"solo": 1, "couple": 2}.get(gtype, 2 if "group_type" in d else 1)
        assumptions.append(f"Group size not given: assumed {size}.")
    if "budget_type" not in d:
        assumptions.append("Budget treated as the total for the whole group.")
    if "budget_flexibility" not in d:
        assumptions.append("Budget treated as a strict limit (no extra spending without your approval).")

    texts = list(d.get("constraints", []))
    constraints = classify_constraints(texts)
    for acc in d.get("accessibility", []):
        constraints.append(Constraint(text=acc, kind="hard", code="accessibility"))

    interests = list(dict.fromkeys(i.strip().lower() for i in d.get("interests", []) if i.strip()))

    prefs = Preferences(
        city=d["city"].strip(),
        starting_location=(d.get("starting_location") or "").strip() or None,
        preferred_areas=d.get("preferred_areas", []),
        travel_mode=d.get("travel_mode", "flexible"),
        group_type=gtype,
        group_size=size,
        group_composition=d.get("group_composition", []),
        budget=d["budget"],
        budget_type=d.get("budget_type", "total"),
        budget_flexibility=d.get("budget_flexibility", "no"),
        date=d.get("date"),
        timing_mode=mode,
        start_time=start,
        end_time=end,
        preferred_period=period or "any",
        flexibility_minutes=flex,
        available_duration_minutes=duration,
        mood=d.get("mood", ""),
        interests=interests,
        constraints=constraints,
    )
    return ParseResult(preferences=prefs, partial=partial, assumptions=assumptions, llm=info)
