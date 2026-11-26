import datetime as dt
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

TravelMode = Literal["walking", "public_transport", "two_wheeler", "car", "taxi", "flexible"]
GroupType = Literal["solo", "couple", "friends", "family", "mixed"]
TimingMode = Literal["exact", "tentative", "period"]
Period = Literal["morning", "afternoon", "evening", "night", "any"]
BudgetFlex = Literal["yes", "no", "ask"]


class Constraint(BaseModel):
    text: str
    kind: Literal["hard", "soft"]
    code: str = "other"
    value: Optional[float] = None  # e.g. max distance in km


class PartialPreferences(BaseModel):
    """Everything optional: what a form, the LLM or a chat turn may supply."""

    model_config = ConfigDict(extra="ignore")

    city: Optional[str] = None
    starting_location: Optional[str] = None
    preferred_areas: Optional[list[str]] = None
    travel_mode: Optional[TravelMode] = None
    group_type: Optional[GroupType] = None
    group_size: Optional[int] = Field(default=None, ge=1, le=50)
    group_composition: Optional[list[str]] = None
    accessibility: Optional[list[str]] = None
    budget: Optional[float] = Field(default=None, gt=0)
    budget_type: Optional[Literal["total", "per_person"]] = None
    budget_flexibility: Optional[BudgetFlex] = None
    date: Optional[dt.date] = None
    timing_mode: Optional[TimingMode] = None
    start_time: Optional[dt.time] = None
    end_time: Optional[dt.time] = None
    preferred_period: Optional[Period] = None
    flexibility_minutes: Optional[int] = Field(default=None, ge=0, le=240)
    available_duration_minutes: Optional[int] = Field(default=None, ge=15, le=1440)
    mood: Optional[str] = None
    interests: Optional[list[str]] = None
    constraints: Optional[list[str]] = None


class Preferences(BaseModel):
    city: str
    starting_location: Optional[str] = None
    preferred_areas: list[str] = []
    travel_mode: TravelMode = "flexible"
    group_type: GroupType = "mixed"
    group_size: int = 1
    group_composition: list[str] = []
    budget: float
    budget_type: Literal["total", "per_person"] = "total"
    budget_flexibility: BudgetFlex = "no"
    date: Optional[dt.date] = None
    timing_mode: TimingMode = "period"
    start_time: Optional[dt.time] = None
    end_time: Optional[dt.time] = None
    preferred_period: Period = "any"
    flexibility_minutes: int = 0
    available_duration_minutes: int
    mood: str = ""
    interests: list[str] = []
    constraints: list[Constraint] = []
    excluded_venue_ids: list[str] = []

    @property
    def total_budget(self) -> float:
        return self.budget * self.group_size if self.budget_type == "per_person" else self.budget

    def has_constraint(self, code: str) -> bool:
        return any(c.code == code for c in self.constraints)

    def constraint(self, code: str) -> Optional[Constraint]:
        return next((c for c in self.constraints if c.code == code), None)
