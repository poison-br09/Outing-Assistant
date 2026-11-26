"""LLM narration (explanations, overview). Every function has a deterministic fallback by design."""
import json

from pydantic import BaseModel

from ..llm.client import try_llm
from ..schemas.api import LLMInfo
from ..schemas.itinerary import PlanProposal
from ..schemas.preferences import Preferences
from .prompts import EXPLAIN_SYSTEM


class _Why(BaseModel):
    id: str
    why: str


class _Explanations(BaseModel):
    stops: list[_Why]


def prefs_facts(p: Preferences) -> dict:
    return {
        "group": f"{p.group_type} of {p.group_size}", "mood": p.mood, "interests": p.interests,
        "hard_constraints": [c.text for c in p.constraints if c.kind == "hard"],
        "soft_preferences": [c.text for c in p.constraints if c.kind == "soft"],
    }


def explain_proposal(proposal: PlanProposal, prefs: Preferences) -> tuple[dict[str, str], LLMInfo]:
    """Friendlier 'why this fits you' text per stop; returns {} (keep deterministic reasons) on failure."""
    facts = {
        "user": prefs_facts(prefs),
        "stops": [{"id": s.venue.id, "name": s.venue.name, "categories": s.venue.categories,
                   "facts": s.why} for s in proposal.itinerary.stops],
    }
    data, info = try_llm(EXPLAIN_SYSTEM, json.dumps(facts), _Explanations)
    if data is None:
        return {}, info
    known = {s.venue.id for s in proposal.itinerary.stops}
    return {w.id: w.why for w in data.stops if w.id in known and w.why.strip()}, info
