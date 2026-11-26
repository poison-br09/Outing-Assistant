"""Tool 10: estimateCost. Every figure is an estimate (providers give no prices); lines say so."""
import math
from collections import defaultdict

from ..config import get_assumptions
from ..schemas.context import RouteEstimate
from ..schemas.itinerary import CostEstimate, CostLine, Itinerary
from ..schemas.preferences import Preferences


def transport_cost(route: RouteEstimate, group: int) -> float:
    f = get_assumptions()["fares"]
    km, m = route.distance_km, route.mode
    if m == "walking" or km <= 0:
        return 0.0
    if m == "taxi":
        t = f["taxi"]
        return (t["base"] + t["per_km"] * km) * math.ceil(group / t["seats"])
    if m in ("car", "two_wheeler"):
        t = f[m]
        return t["per_km"] * km * math.ceil(group / t["seats"])
    t = f["public_transport"]
    return max(t["min_fare"], t["per_km"] * km) * group


def estimate_cost(it: Itinerary, prefs: Preferences) -> CostEstimate:
    a = get_assumptions()
    g, cur = prefs.group_size, a["currency"]
    lines: list[CostLine] = []
    for s in it.stops:
        pp = s.venue.est_cost_per_person
        if s.venue.kind == "food":
            lines.append(CostLine(label=f"Food & drinks: {s.venue.name} ({g} × {cur}{pp:.0f})", category="food", amount=round(pp * g)))
        else:
            label = f"Activity: {s.venue.name} ({g} × {cur}{pp:.0f})" if pp else f"Activity: {s.venue.name} (no admission assumed)"
            lines.append(CostLine(label=label, category="activity", amount=round(pp * g)))

    by_mode: dict[str, float] = defaultdict(float)
    legs: dict[str, int] = defaultdict(int)
    parking = 0.0
    for r in [s.route for s in it.stops if s.route] + ([it.return_route] if it.return_route else []):
        by_mode[r.mode] += transport_cost(r, g)
        legs[r.mode] += 1
    for s in it.stops:
        if s.route and s.route.mode in ("car", "two_wheeler") and s.route.distance_km > 0:
            parking += a["fares"][s.route.mode].get("parking", 0)
    for mode, amt in by_mode.items():
        if amt > 0:
            lines.append(CostLine(label=f"Transport by {mode.replace('_', ' ')} ({legs[mode]} legs incl. return)", category="transport", amount=round(amt)))
    if parking:
        lines.append(CostLine(label="Parking (estimate)", category="parking", amount=round(parking)))

    subtotal = float(sum(l.amount for l in lines))
    buffer = round(subtotal * a["buffer_pct"])
    if buffer:
        lines.append(CostLine(label=f"Buffer ({a['buffer_pct']:.0%}) for small extras", category="buffer", amount=buffer))
    total = subtotal + buffer
    budget = prefs.total_budget
    ceiling = round(budget * (1 + a["budget_ceiling_pct"]), 2)
    return CostEstimate(
        lines=lines, subtotal=subtotal, buffer=buffer, total=total, per_person=round(total / g),
        budget=budget, ceiling=ceiling, within_budget=total <= budget, within_ceiling=total <= ceiling,
        over_by=round(max(total - budget, 0), 2), currency=cur,
    )
