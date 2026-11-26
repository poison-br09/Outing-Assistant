"""Venue verification: fill missing opening hours/website from the provider's Place Details API.

This improves coverage (fewer 'unverified' venues). It is still provider data, never a live
check with the venue, so results stay labelled as unverified-with-source.
"""
import logging
from concurrent.futures import ThreadPoolExecutor

from pydantic import BaseModel

from ..config import get_settings
from ..schemas.itinerary import Venue
from ..services.geo import ProviderError, fetch_place_details

log = logging.getLogger(__name__)
MAX_LOOKUPS = 15


class VerifyReport(BaseModel):
    attempted: int = 0
    enriched: int = 0
    failed: int = 0
    skipped_reason: str | None = None


def _enrich(v: Venue) -> bool | None:
    try:
        d = fetch_place_details(v.id)
    except ProviderError as e:
        log.warning("place details failed: %s", e)
        return None
    hours = d.get("opening_hours") or (d.get("datasource") or {}).get("raw", {}).get("opening_hours")
    if not v.website:
        v.website = d.get("website") or (d.get("contact") or {}).get("website")
    if hours:
        v.opening_hours, v.hours_source = hours, "place_details"
        return True
    return False


def verify_venues(venues: list[Venue], max_lookups: int = MAX_LOOKUPS) -> VerifyReport:
    """Mutates venues in place. Only venues without hours are looked up (bounded, parallel)."""
    need = [v for v in venues if not v.opening_hours][:max_lookups]
    rep = VerifyReport(attempted=len(need))
    if not need:
        return rep
    if not get_settings().geoapify_api_key:
        rep.attempted, rep.skipped_reason = 0, "venue provider key not configured"
        return rep
    with ThreadPoolExecutor(max_workers=5) as ex:
        results = list(ex.map(_enrich, need))
    rep.enriched = sum(1 for r in results if r)
    rep.failed = sum(1 for r in results if r is None)
    return rep
