"""Stable HTTP contract for the stateless course-selection game model."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class CourseSelectionPolicyModel(StrictModel):
    budget: int = Field(default=105, ge=1, le=150)
    min_bid: int = Field(default=5, ge=1, le=150)
    bid_step: int = Field(default=1, ge=1, le=50)
    tie_rule: Literal["random", "conservative"] = "random"
    max_selected_courses: int | None = Field(default=None, ge=1, le=100)
    demand_multipliers: tuple[float, float, float] = (0.8, 1.0, 1.2)

    @model_validator(mode="after")
    def validate_policy(self):
        if self.min_bid > self.budget:
            raise ValueError("min_bid cannot exceed budget")
        if self.budget % self.bid_step or self.min_bid % self.bid_step:
            raise ValueError("budget and min_bid must align to bid_step")
        low, base, high = self.demand_multipliers
        if not 0.25 <= low < base < high <= 3.0 or abs(base - 1.0) > 1e-9:
            raise ValueError("demand_multipliers must be ordered around a 1.0 base")
        return self


class CourseMarketModel(StrictModel):
    course_id: str = Field(min_length=1, max_length=64, pattern=r"^[^\x00-\x1f\x7f]+$")
    name: str = Field(default="", max_length=200)
    capacity: int = Field(ge=1, le=100_000)
    current_participants: int = Field(ge=0, le=100_000)
    target_included: bool = False
    target_interested: bool = False
    target_utility: float | None = Field(default=None, gt=0, le=100)

    @model_validator(mode="after")
    def validate_target(self):
        if self.target_included and self.current_participants == 0:
            raise ValueError("target_included requires a positive participant count")
        if self.target_utility is not None and not self.target_interested:
            raise ValueError("target_utility is only valid for an interested course")
        return self


class CourseMarketSnapshotModel(StrictModel):
    cohort_size: int = Field(ge=1, le=100_000)
    captured_at: datetime
    is_complete: bool
    courses: list[CourseMarketModel] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_market(self):
        if not self.is_complete:
            raise ValueError("a complete course market is required")
        if self.captured_at.tzinfo is None or self.captured_at.utcoffset() is None:
            raise ValueError("captured_at must include a timezone")
        ids = [course.course_id for course in self.courses]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate course_id")
        if not any(course.target_interested for course in self.courses):
            raise ValueError("at least one target course is required")
        for course in self.courses:
            if course.current_participants > self.cohort_size:
                raise ValueError(f"participant count exceeds cohort_size: {course.course_id}")
        return self


class CourseSelectionOptimizeRequest(StrictModel):
    policy: CourseSelectionPolicyModel = Field(default_factory=CourseSelectionPolicyModel)
    market: CourseMarketSnapshotModel

    @model_validator(mode="after")
    def validate_workload(self):
        legal_bid_count = (self.policy.budget - self.policy.min_bid) // self.policy.bid_step + 1
        interested = sum(course.target_interested for course in self.market.courses)
        max_selected = min(
            interested,
            self.policy.max_selected_courses or self.policy.budget // self.policy.min_bid,
        )
        if len(self.market.courses) * legal_bid_count > 3_000:
            raise ValueError("market and bid-grid combination is too large")
        if interested > 30 or interested * self.policy.budget * legal_bid_count * max_selected > 8_000_000:
            raise ValueError("target-course optimization workload is too large")
        return self


class ScenarioDiagnosticModel(StrictModel):
    scenario: Literal["optimistic", "baseline", "pessimistic"]
    total_entries: int = Field(ge=0)
    average_entries: float = Field(ge=0)
    typical_bid: float = Field(gt=0)


class StrategyCourseResultModel(StrictModel):
    course_id: str
    name: str
    bid: int = Field(ge=0)
    classification: Literal["uncontested", "competitive", "omitted"]
    utility: float = Field(gt=0)
    scenario_success_rates: dict[str, float]
    success_rate_range: dict[str, float]
    expected_utility: float = Field(ge=0)
    marginal_gain: float = Field(ge=0)
    redundant_bid: int = Field(ge=0)
    forecast_participants: dict[str, int]


class CourseSelectionStrategyModel(StrictModel):
    name: Literal["robust", "balanced", "aggressive"]
    budget_used: int = Field(ge=0)
    objective_value: float = Field(ge=0)
    scenario_expected_utilities: dict[str, float]
    courses: list[StrategyCourseResultModel]


class CourseSelectionOptimizeResponse(StrictModel):
    model_version: str
    market_confidence: Literal["uncalibrated_reference_proxy"]
    solution_status: Literal["solved"]
    warnings: list[str]
    assumptions: list[str]
    scenario_multipliers: dict[str, float]
    diagnostics: list[ScenarioDiagnosticModel]
    strategies: list[CourseSelectionStrategyModel]


class JwxkSettingsUpdate(StrictModel):
    network_mode: Literal["follow", "direct", "webvpn"]


class JwxkBatchModel(StrictModel):
    code: str
    name: str
    term_code: str
    term_name: str
    begin_time: str
    end_time: str
    selection_type: str
    selection_type_code: str
    tactic_name: str
    course_types: list[str]
    need_confirm: bool
    notice: str
    state: Literal["not_started", "active", "ended", "unknown"]
    can_enter: bool


class JwxkStatusResponse(StrictModel):
    available: bool
    network_mode: Literal["follow", "direct", "webvpn"]
    effective_network_mode: Literal["direct", "webvpn"]
    cas_service: str
    primary_authenticated: bool = False
    service_authenticated: bool = False
    authenticated: bool = False
    batches: list[JwxkBatchModel]
    message: str = ""
