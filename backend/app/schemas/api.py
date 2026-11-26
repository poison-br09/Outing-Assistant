import datetime as dt
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from .context import TemporalContext, WeatherReport
from .itinerary import PlanProposal
from .preferences import Preferences


class LLMInfo(BaseModel):
    used: bool = False
    model: Optional[str] = None
    attempts: int = 0
    latency_ms: int = 0
    fallback_reason: Optional[str] = None  # set when deterministic logic replaced the LLM


class TraceStep(BaseModel):
    id: int
    name: str
    status: Literal["pending", "running", "success", "warning", "failed"] = "running"
    summary: str = ""
    detail: dict[str, Any] = {}
    duration_ms: int = 0


class PlanRequest(BaseModel):
    fields: dict[str, Any] = Field(default_factory=dict, description="Structured fields (form or carried chat state)")
    message: Optional[str] = Field(default=None, description="Free text (chat message or form notes)")
    message_overrides: bool = Field(default=False, description="True for chat: extracted values override `fields`")
    excluded_venue_ids: list[str] = []


class AgentResponse(BaseModel):
    status: Literal["needs_clarification", "proposal", "fallback"]
    message: str = ""
    questions: list[str] = []
    partial: dict[str, Any] = {}
    preferences: Optional[Preferences] = None
    temporal: Optional[TemporalContext] = None
    weather: Optional[WeatherReport] = None
    proposals: list[PlanProposal] = []
    recommended_id: Optional[str] = None
    warnings: list[str] = []
    assumptions: list[str] = []
    generic_suggestions: list[str] = []
    llm: list[LLMInfo] = []
    trace: list[TraceStep] = []
    currency: str = "₹"


class TimelineEntry(BaseModel):
    time: str
    title: str
    kind: str
    address: Optional[str] = None
    map_link: Optional[str] = None
    duration_min: int
    travel_note: Optional[str] = None
    why: str = ""
    availability: str = "unverified"


class FinalPlan(BaseModel):
    overview: str
    timeline: list[TimelineEntry]
    cost_lines: list[dict[str, Any]]
    total_cost: float
    per_person: float
    currency: str
    practical_notes: list[str]
    caveats: list[str]
    llm: LLMInfo
    route_link: Optional[str] = None
    calendar_link: Optional[str] = None
    ics: Optional[str] = None
    trace: list[TraceStep] = []


class FinalizeRequest(BaseModel):
    preferences: Preferences
    proposal: PlanProposal
    temporal: TemporalContext
    weather: Optional[WeatherReport] = None
    approved: bool = False
    accept_extra_budget: bool = False


class TripRequest(BaseModel):
    plan: PlanRequest
    days: int = Field(ge=2, le=5, description="Number of consecutive days to plan")
    fixed_costs: float = Field(default=0, ge=0, description="Lodging, intercity travel etc. supplied by the user")


class DayPlan(BaseModel):
    day: int
    date: Optional[dt.date] = None
    response: AgentResponse


class TripResponse(BaseModel):
    status: Literal["needs_clarification", "proposal", "fallback"]
    message: str = ""
    questions: list[str] = []
    days: list[DayPlan] = []
    trip_budget: float = 0.0
    fixed_costs: float = 0.0
    estimated_total: float = 0.0  # recommended plan per day + fixed costs
    within_budget: bool = True
    warnings: list[str] = []
    assumptions: list[str] = []
    currency: str = "₹"
