import datetime as dt
from typing import Literal, Optional

from pydantic import BaseModel

from .context import Availability, CrowdEstimate, RouteEstimate


class Venue(BaseModel):
    id: str
    name: str
    address: Optional[str] = None
    lat: float
    lon: float
    categories: list[str] = []
    kind: Literal["activity", "food"]
    indoor: bool = True
    opening_hours: Optional[str] = None
    website: Optional[str] = None
    distance_m: float = 0.0
    matched_interest: Optional[str] = None
    est_cost_per_person: float = 0.0
    cost_basis: Literal["estimate"] = "estimate"  # providers give no prices
    diet_tags: list[str] = []
    diet_verified: bool = False
    source: str = "geoapify"
    verified: bool = False  # hours/prices are never live-verified


class RankedVenue(BaseModel):
    venue: Venue
    score: float
    components: dict[str, float] = {}
    reasons: list[str] = []
    crowd: Optional[CrowdEstimate] = None


class Stop(BaseModel):
    venue: Venue
    start: dt.datetime
    end: dt.datetime
    duration_min: int
    route: Optional[RouteEstimate] = None  # travel from the previous point
    availability: Optional[Availability] = None
    crowd: Optional[CrowdEstimate] = None
    why: str = ""


class Itinerary(BaseModel):
    variant: str
    departs_at: dt.datetime
    stops: list[Stop]
    return_route: Optional[RouteEstimate] = None
    total_travel_min: float = 0.0
    notes: list[str] = []

    @property
    def ends_at(self) -> dt.datetime:
        return self.stops[-1].end if self.stops else self.departs_at


class CostLine(BaseModel):
    label: str
    category: Literal["activity", "food", "transport", "parking", "buffer"]
    amount: float
    basis: Literal["estimate", "sourced"] = "estimate"


class CostEstimate(BaseModel):
    lines: list[CostLine]
    subtotal: float
    buffer: float
    total: float
    per_person: float
    budget: float
    ceiling: float
    within_budget: bool
    within_ceiling: bool
    over_by: float
    currency: str


class Check(BaseModel):
    name: str
    status: Literal["pass", "warn", "fail"]
    message: str


class ValidationResult(BaseModel):
    passed: bool
    requires_budget_approval: bool = False
    checks: list[Check]

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.checks if c.status == "fail"]

    @property
    def warnings(self) -> list[Check]:
        return [c for c in self.checks if c.status == "warn"]


class PlanProposal(BaseModel):
    id: str
    variant: Literal["budget_friendly", "balanced", "experience_first", "low_cost"]
    title: str
    itinerary: Itinerary
    cost: CostEstimate
    validation: ValidationResult
    tradeoffs: list[str] = []
    requires_budget_approval: bool = False
    score: float = 0.0
