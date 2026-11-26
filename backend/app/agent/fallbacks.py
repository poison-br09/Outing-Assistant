"""Generic, clearly labelled suggestions used when venue data is unavailable or nothing matches.

These are ideas, never venue names, prices or hours.
"""
from typing import Optional

from ..config import get_assumptions
from ..schemas.preferences import Preferences


def generic_suggestions(prefs: Optional[Preferences]) -> list[str]:
    ideas = get_assumptions()["generic_ideas"]
    interests = [i for i in (prefs.interests if prefs else []) if i in ideas] or ["walks", "food", "culture"]
    out = [f"Generic idea ({i}): {ideas[i]}" for i in interests[:4]]
    if prefs:
        if prefs.has_constraint("vegetarian") or prefs.has_constraint("vegan"):
            out.append("Generic tip: look for restaurants that list vegetarian/vegan menus, and confirm with the venue.")
        if prefs.has_constraint("avoid_crowds"):
            out.append("Generic tip: weekday-like quiet hours (mid-afternoon) are usually calmer than meal peaks.")
    return out
