"""Stable HTTP contract for the stateless course-selection game model."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

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
    account_selectable: bool = False
    confirmed: bool = False
    week_range: str = ""
    allow_conflict: bool = False
    allow_cross_campus: bool = False
    menus: list[dict[str, str]] = Field(default_factory=list)


class JwxkStatusResponse(StrictModel):
    available: bool
    network_mode: Literal["follow", "direct", "webvpn"]
    effective_network_mode: Literal["direct", "webvpn"]
    cas_service: str
    primary_authenticated: bool = False
    service_authenticated: bool = False
    authenticated: bool = False
    official_time: str = ""
    online_count: int | None = None
    current_campus: str = ""
    current_campus_name: str = ""
    batches: list[JwxkBatchModel]
    message: str = ""


class JwxkBatchRequest(StrictModel):
    batch_code: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


class JwxkCourseSearchRequest(JwxkBatchRequest):
    teaching_class_type: str = Field(min_length=1, max_length=24, pattern=r"^[A-Z0-9_]+$")
    page_number: int = Field(default=1, ge=1, le=1000)
    page_size: int = Field(default=20, ge=1, le=50)
    keyword: str = Field(default="", max_length=100)
    campus: str = Field(default="", max_length=40)
    order_by: str = Field(default="", max_length=34)
    filters: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_filters(self):
        if len(self.filters) > 16:
            raise ValueError("too many course filters")
        for key, value in self.filters.items():
            if len(key) > 32 or len(value) > 100:
                raise ValueError("course filter is too long")
        return self


class JwxkCourseItem(StrictModel):
    group_id: str = ""
    course_code: str = ""
    course_name: str = ""
    class_id: str = ""
    class_number: str = ""
    teaching_class_type: str = ""
    credits: str = ""
    hours: str = ""
    teacher: str = ""
    location: str = ""
    official_schedule: str = ""
    campus: str = ""
    campus_name: str = ""
    department: str = ""
    course_nature: str = ""
    course_category: str = ""
    course_categories: list[str] = Field(default_factory=list)
    normalized_course_category: str = ""
    general_elective_category_code: str = ""
    general_elective_category: str = ""
    exam_type_code: str = ""
    exam_type: str = ""
    score_scale_code: str = ""
    score_scale: str = ""
    teaching_mode: str = ""
    teacher_details: list[dict[str, str]] = Field(default_factory=list)
    teacher_titles: str = ""
    target_classes: list[str] = Field(default_factory=list)
    capacity: int | None = None
    selected_count: int | None = None
    first_choice_count: int | None = None
    weight_participant_count: int | None = None
    selection_type_code: str = ""
    market_participant_count: int | None = None
    market_participant_label: str = ""
    capacity_updated_at: str = ""
    devoted_weight: int | None = None
    selection_source: str = ""
    conflict: bool = False
    conflict_description: str = ""
    restricted: bool = False
    eligibility_status: Literal["unknown", "selectable", "unavailable"] = "unknown"
    eligibility_reason: str = ""
    full: bool = False
    selected: bool = False
    course_already_selected: bool = False
    has_test: bool = False
    has_book: bool = False
    notice: str = ""
    source_tags: list[str] = Field(default_factory=list)
    campuses: list[str] = Field(default_factory=list)
    source_scopes: list[str] = Field(default_factory=list)
    schedules: list[dict[str, Any]] = Field(default_factory=list)


class JwxkCourseSearchResponse(StrictModel):
    total: int = Field(ge=0)
    courses: list[JwxkCourseItem]


class JwxkSelectedResponse(StrictModel):
    selected: list[JwxkCourseItem]
    volunteered: list[JwxkCourseItem]
    withdrawal: list[JwxkCourseItem]


class JwxkBatchConfirmRequest(JwxkBatchRequest):
    acknowledged: Literal[True]


class JwxkCourseSelectRequest(JwxkBatchRequest):
    teaching_class_type: str = Field(min_length=1, max_length=24, pattern=r"^[A-Z0-9_]+$")
    class_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    course_code: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    weight: int | None = Field(default=None, ge=5, le=150)
    confirm_risk: bool = False
    preflight_verified: bool = False


class JwxkCourseDeselectRequest(JwxkBatchRequest):
    class_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    selection_source: Literal["yxkcyx", "fakcyx", "xgxkyx", ""] = ""
    confirm_risk: bool = False


class JwxkMutationResponse(StrictModel):
    success: bool
    queued: bool
    requires_confirmation: bool
    code: str
    message: str


class JwxkTimeSlot(StrictModel):
    weekday: int = Field(ge=1, le=7)
    section: int = Field(ge=1, le=30)


class JwxkCatalogSearchRequest(JwxkBatchRequest):
    page_number: int = Field(default=1, ge=1, le=1000)
    page_size: int = Field(default=20, ge=1, le=50)
    keyword: str = Field(default="", max_length=100)
    scope: str = Field(default="ALL", min_length=1, max_length=24, pattern=r"^[A-Z0-9_]+$")
    campus: str = Field(default="", max_length=40)
    order_by: str = Field(default="", max_length=34)
    filters: dict[str, str] = Field(default_factory=dict)
    time_slot: JwxkTimeSlot | None = None
    local_only: bool = False


class JwxkCatalogDetailRequest(JwxkBatchRequest):
    teaching_class_type: str = Field(min_length=1, max_length=24, pattern=r"^[A-Z0-9_]+$")
    course_code: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    class_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


class JwxkEligibilityRequest(JwxkBatchRequest):
    class_ids: list[str] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def validate_class_ids(self):
        if any(
            not value or len(value) > 64 or not value.replace("-", "").replace("_", "").isalnum()
            for value in self.class_ids
        ):
            raise ValueError("invalid teaching class id")
        return self


class JwxkEligibilityResult(StrictModel):
    class_id: str
    status: Literal["unknown", "selectable", "unavailable"]
    reason: str = ""


class JwxkEligibilityResponse(StrictModel):
    results: list[JwxkEligibilityResult] = Field(default_factory=list)


class JwxkCourseGroup(StrictModel):
    group_id: str
    course_code: str = ""
    course_name: str
    credits: str = ""
    hours: str = ""
    department: str = ""
    course_nature: str = ""
    course_category: str = ""
    course_categories: list[str] = Field(default_factory=list)
    normalized_course_category: str = ""
    general_elective_category_code: str = ""
    general_elective_category: str = ""
    exam_type_code: str = ""
    exam_type: str = ""
    score_scale_code: str = ""
    score_scale: str = ""
    campuses: list[str] = Field(default_factory=list)
    source_tags: list[str] = Field(default_factory=list)
    class_count: int = 0
    selectable_count: int = 0
    eligibility_pending_count: int = 0
    available_count: int = 0
    conflict_free_count: int = 0
    classes: list[JwxkCourseItem] = Field(default_factory=list)


class JwxkCatalogSearchResponse(StrictModel):
    total: int = 0
    scope: str
    scope_options: list[dict[str, str]] = Field(default_factory=list)
    groups: list[JwxkCourseGroup] = Field(default_factory=list)
    cache_hit: bool = False
    data_source: Literal["local", "remote"] = "remote"
    sync_status: str = ""


class JwxkCourseDetail(StrictModel):
    course_code: str = ""
    course_name: str = ""
    english_name: str = ""
    credits: str = ""
    hours: str = ""
    department: str = ""
    course_nature: str = ""
    course_category: str = ""
    course_categories: list[str] = Field(default_factory=list)
    normalized_course_category: str = ""
    general_elective_category_code: str = ""
    general_elective_category: str = ""
    exam_type_code: str = ""
    exam_type: str = ""
    score_scale_code: str = ""
    score_scale: str = ""
    description: str = ""


class JwxkCatalogDetailResponse(StrictModel):
    course: JwxkCourseDetail
    teaching_class: JwxkCourseItem


class JwxkSelectionScheduleResponse(StrictModel):
    source: Literal["official_timetable", "selected_records_fallback"]
    source_label: str
    courses: list[JwxkCourseItem] = Field(default_factory=list)
    meetings: list[dict[str, Any]] = Field(default_factory=list)


class JwxkPlanPreviewRequest(JwxkBatchRequest):
    term_code: str = Field(min_length=1, max_length=32)
    meetings: list[dict[str, Any]] = Field(default_factory=list, max_length=200)


class JwxkPlanPreviewResponse(StrictModel):
    term_code: str
    baseline_available: bool
    baseline_stale: bool
    results: list[dict[str, Any]] = Field(default_factory=list)


class JwxkPlanGroup(StrictModel):
    group_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=1, max_length=60)
    target_count: int = Field(default=1, ge=1, le=20)


class JwxkSavedPlanRequest(JwxkBatchRequest):
    term_code: str = Field(min_length=1, max_length=32)
    groups: list[JwxkPlanGroup] = Field(default_factory=list, max_length=20)
    items: list[dict[str, Any]] = Field(default_factory=list, max_length=100)


class JwxkWeightPlanRequest(JwxkSavedPlanRequest):
    grade_size: int = Field(ge=1, le=100_000)


class JwxkWeightConfigResponse(StrictModel):
    term_code: str
    grade_size: int | None = None


class JwxkAutomationSettings(StrictModel):
    """Account/round-scoped automation and notification preferences."""
    strategy_schedule_mode: Literal["interval", "final_windows"] = "interval"
    rebalance_seconds: int = Field(default=600, ge=600, le=86400)
    force_final_rebalance: bool = True
    mail_enabled: bool = False
    notify_round_end: bool = False
    notify_final_rebalance: bool = False
    notify_capacity_transition: bool = False
    notify_over_capacity: bool = False
    notify_underfilled_warning: bool = False
    notify_grab_result: bool = False
    over_capacity_ratio: float = Field(default=0.20, ge=0, le=10)


class JwxkAutomationSettingsResponse(JwxkAutomationSettings):
    batch_code: str
    batch_name: str = ""
    term_code: str = ""
    selection_type_code: str = ""
    smtp_configured: bool = False
    smtp_status: str = "未配置"


class JwxkAutomationCourseRef(StrictModel):
    class_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    course_code: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    course_name: str = Field(default="", max_length=120)
    teaching_class_type: str = Field(default="ALLKC", min_length=1, max_length=24, pattern=r"^[A-Z0-9_]+$")
    teacher: str = Field(default="", max_length=120)


class JwxkVacancySwapGroup(StrictModel):
    group_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=1, max_length=80)
    target: JwxkAutomationCourseRef
    drop_courses: list[JwxkAutomationCourseRef] = Field(min_length=1, max_length=20)


class JwxkAutomationTaskRequest(JwxkSavedPlanRequest):
    name: str = Field(default="自动抢课任务", min_length=1, max_length=80)
    task_type: Literal["selection", "vacancy_swap", "weight_strategy"] = "selection"
    swap_groups: list[JwxkVacancySwapGroup] = Field(default_factory=list, max_length=20)
    grade_size: int | None = Field(default=None, ge=1, le=100_000)
    rebalance_seconds: int = Field(default=60, ge=60, le=600)
    start_at: str = ""
    end_at: str = ""
    # One second is reserved for the short opening burst.  Normal vacancy
    # monitoring is clamped to at least 15 seconds by the service.
    poll_seconds: int = Field(default=15, ge=1, le=300)

    @model_validator(mode="after")
    def validate_task_mode(self):
        if self.task_type == "weight_strategy" and self.grade_size is None:
            raise ValueError("weight strategy requires grade_size")
        return self


class JwxkAutomationTaskAction(StrictModel):
    task_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


class JwxkAutomationTaskTimeSyncRequest(JwxkBatchRequest):
    start_at: str = Field(min_length=1, max_length=40)
    end_at: str = Field(min_length=1, max_length=40)

    @model_validator(mode="after")
    def validate_time_range(self):
        try:
            start = datetime.fromisoformat(self.start_at)
            end = datetime.fromisoformat(self.end_at)
        except ValueError as error:
            raise ValueError("invalid automation task time") from error
        if end <= start:
            raise ValueError("automation task end time must be after start time")
        return self
