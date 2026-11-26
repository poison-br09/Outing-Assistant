"""Convert provider place records into Venue objects (shared by the finder tools)."""
from typing import Optional

from ..config import get_assumptions
from ..schemas.context import Point
from ..schemas.itinerary import Venue
from ..utils import category_lookup, has_category, haversine_km


def to_venue(props: dict, kind: str, origin: Point, interest: Optional[str] = None) -> Optional[Venue]:
    name, lat, lon = props.get("name"), props.get("lat"), props.get("lon")
    if not name or lat is None or lon is None:
        return None
    a = get_assumptions()
    cats = props.get("categories") or []
    raw = (props.get("datasource") or {}).get("raw") or {}
    diet = (props.get("catering") or {}).get("diet") or {}
    tags = {k for k, v in diet.items() if v}
    for k in ("vegetarian", "vegan"):
        if str(raw.get(f"diet:{k}", "")).lower() in ("yes", "only"):
            tags.add(k)
    if "vegan" in tags:
        tags.add("vegetarian")
    hours = props.get("opening_hours") or raw.get("opening_hours")
    return Venue(
        id=props.get("place_id") or f"{name}:{lat:.5f}:{lon:.5f}",
        name=name,
        address=props.get("formatted") or props.get("address_line2"),
        lat=lat, lon=lon, categories=cats, kind=kind,  # type: ignore[arg-type]
        indoor=not has_category(cats, a["outdoor_categories"]),
        opening_hours=hours, hours_source="search" if hours else "none",
        website=props.get("website") or (props.get("contact") or {}).get("website"),
        distance_m=round(haversine_km(origin.lat, origin.lon, lat, lon) * 1000),
        matched_interest=interest,
        est_cost_per_person=float(category_lookup(a["price_per_person"], cats, a["default_price_per_person"])),
        diet_tags=sorted(tags),
    )
