"""Stable HTTP contracts for timetable queries."""

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


TimetableMode = Literal["personal", "class", "teacher", "room"]
TargetMode = Literal["class", "teacher", "room"]


class TimetableTermModel(BaseModel):
    code: str
    name: str
    current: bool = False


class TimetableTermsResponse(BaseModel):
    terms: List[TimetableTermModel]
    current: Optional[str] = None


class TimetableContextRequest(BaseModel):
    mode: TimetableMode = "personal"
    term_code: str = Field(min_length=1, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")
    target_id: str = Field(default="", max_length=128)
    campus_code: str = Field(default="", max_length=64)


class TimetableCampusModel(BaseModel):
    code: str
    name: str


class TimetableWeekModel(BaseModel):
    number: int
    name: str
    start_date: str = ""
    end_date: str = ""
    current: bool = False


class TimetableSectionModel(BaseModel):
    number: int
    name: str
    start_time: str = ""
    end_time: str = ""


class TimetableContextResponse(BaseModel):
    campuses: List[TimetableCampusModel]
    weeks: List[TimetableWeekModel]
    sections: List[TimetableSectionModel]


class TimetableTargetSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: TargetMode
    term_code: str = Field(min_length=1, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")
    keyword: str = Field(default="", max_length=100, pattern=r"^[^\x00-\x1f\x7f]*$")
    page: int = Field(default=1, ge=1, le=10000)
    page_size: int = Field(default=20, ge=1, le=50)
    filters: "TimetableTargetFilters" = Field(default_factory=lambda: TimetableTargetFilters())

    @model_validator(mode="after")
    def validate_mode_filters(self):
        allowed = {
            "class": {"grade", "college", "major", "direction", "campus", "has_schedule"},
            "teacher": {"department", "title", "gender", "external", "has_schedule"},
            "room": {
                "campus", "building", "floor", "room_type", "department", "use_scope",
                "lab_center", "min_capacity", "max_capacity", "has_schedule",
            },
        }[self.mode]
        supplied = set(self.filters.model_dump(exclude_none=True, exclude_defaults=True))
        if not supplied <= allowed:
            raise ValueError(f"{self.mode} mode does not support filters: {sorted(supplied - allowed)}")
        return self


class TimetableTargetFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    grade: Optional[str] = Field(default=None, max_length=64, pattern=r"^[^\x00-\x1f\x7f]*$")
    college: Optional[str] = Field(default=None, max_length=64, pattern=r"^[^\x00-\x1f\x7f]*$")
    major: Optional[str] = Field(default=None, max_length=64, pattern=r"^[^\x00-\x1f\x7f]*$")
    direction: Optional[str] = Field(default=None, max_length=64, pattern=r"^[^\x00-\x1f\x7f]*$")
    campus: Optional[str] = Field(default=None, max_length=64, pattern=r"^[^\x00-\x1f\x7f]*$")
    department: Optional[str] = Field(default=None, max_length=64, pattern=r"^[^\x00-\x1f\x7f]*$")
    title: Optional[str] = Field(default=None, max_length=64, pattern=r"^[^\x00-\x1f\x7f]*$")
    gender: Optional[str] = Field(default=None, max_length=16, pattern=r"^[^\x00-\x1f\x7f]*$")
    external: Optional[str] = Field(default=None, max_length=16, pattern=r"^[^\x00-\x1f\x7f]*$")
    building: Optional[str] = Field(default=None, max_length=64, pattern=r"^[^\x00-\x1f\x7f]*$")
    floor: Optional[str] = Field(default=None, max_length=32, pattern=r"^[^\x00-\x1f\x7f]*$")
    room_type: Optional[str] = Field(default=None, max_length=64, pattern=r"^[^\x00-\x1f\x7f]*$")
    use_scope: Optional[str] = Field(default=None, max_length=64, pattern=r"^[^\x00-\x1f\x7f]*$")
    lab_center: Optional[str] = Field(default=None, max_length=64, pattern=r"^[^\x00-\x1f\x7f]*$")
    min_capacity: Optional[int] = Field(default=None, ge=0, le=10000)
    max_capacity: Optional[int] = Field(default=None, ge=0, le=10000)
    has_schedule: Optional[Literal["yes", "no"]] = None

    @model_validator(mode="after")
    def validate_capacity_range(self):
        if (
            self.min_capacity is not None
            and self.max_capacity is not None
            and self.min_capacity > self.max_capacity
        ):
            raise ValueError("min_capacity cannot exceed max_capacity")
        return self


class TimetableTargetModel(BaseModel):
    id: str
    name: str
    has_schedule: str = ""
    details: Dict[str, str] = Field(default_factory=dict)
    filter_values: Dict[str, str] = Field(default_factory=dict)


class TimetableTargetSearchResponse(BaseModel):
    items: List[TimetableTargetModel]
    total: int
    page: int
    page_size: int


class TimetableTargetFilterOptionsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: TargetMode
    term_code: str = Field(min_length=1, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")
    # 只加载当前筛选层级需要的字段，避免打开筛选器时扫描完整对象目录。
    keys: List[str] = Field(default_factory=list, max_length=20)
    filters: "TimetableTargetFilters" = Field(default_factory=lambda: TimetableTargetFilters())

    @model_validator(mode="after")
    def validate_filter_options(self):
        allowed_by_mode = {
            "class": {"grade", "college", "major", "direction", "campus", "has_schedule"},
            "teacher": {"department", "title", "gender", "external", "has_schedule"},
            "room": {"campus", "building", "floor", "room_type", "department", "use_scope", "lab_center", "min_capacity", "max_capacity", "has_schedule"},
        }
        allowed = allowed_by_mode[self.mode]
        requested = set(self.keys)
        if requested and not requested <= allowed:
            raise ValueError(f"unsupported filter option keys: {sorted(requested - allowed)}")
        supplied = set(self.filters.model_dump(exclude_none=True, exclude_defaults=True))
        if not supplied <= allowed:
            raise ValueError(f"unsupported filter options: {sorted(supplied - allowed)}")
        return self


class TimetableTargetFilterOption(BaseModel):
    value: str
    label: str


class TimetableTargetFilterOptionsResponse(BaseModel):
    options: Dict[str, List[TimetableTargetFilterOption]] = Field(default_factory=dict)
    relations: List[Dict[str, str]] = Field(default_factory=list)


class TimetableScheduleRequest(TimetableContextRequest):
    campus_code: str = Field(min_length=1, max_length=64)
    week: Optional[int] = Field(default=None, ge=1, le=30)


class TimetableRoomAvailabilitySlot(BaseModel):
    # Range fields are inclusive. Legacy week/weekday fields remain accepted.
    week_start: Optional[int] = Field(default=None, ge=1, le=30)
    week_end: Optional[int] = Field(default=None, ge=1, le=30)
    weekday_start: Optional[int] = Field(default=None, ge=1, le=7)
    weekday_end: Optional[int] = Field(default=None, ge=1, le=7)
    start_section: Optional[int] = Field(default=None, ge=1, le=30)
    end_section: Optional[int] = Field(default=None, ge=1, le=30)
    week: Optional[int] = Field(default=None, ge=1, le=30)
    weekday: Optional[int] = Field(default=None, ge=1, le=7)
    joiner: Literal["and", "or"] = "and"

    @model_validator(mode="after")
    def validate_sections(self):
        if self.week is not None:
            if self.week_start is None: self.week_start = self.week
            if self.week_end is None: self.week_end = self.week
        if self.weekday is not None:
            if self.weekday_start is None: self.weekday_start = self.weekday
            if self.weekday_end is None: self.weekday_end = self.weekday
        if self.week_start is not None and self.week_end is not None and self.week_start > self.week_end:
            raise ValueError("week_start cannot exceed week_end")
        weekday_order = {7: 0, 1: 1, 2: 2, 3: 3, 4: 4, 5: 5, 6: 6}
        if (
            self.weekday_start is not None
            and self.weekday_end is not None
            and weekday_order[self.weekday_start] > weekday_order[self.weekday_end]
        ):
            raise ValueError("weekday_start cannot follow weekday_end")
        if self.start_section is not None and self.end_section is not None and self.start_section > self.end_section:
            raise ValueError("start_section cannot exceed end_section")
        return self


class TimetableRoomAvailabilityRequest(BaseModel):
    term_code: str = Field(min_length=1, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")
    campus_code: str = Field(min_length=1, max_length=64)
    filters: TimetableTargetFilters = Field(default_factory=TimetableTargetFilters)
    slots: List[TimetableRoomAvailabilitySlot] = Field(min_length=1, max_length=20)
    keyword: str = Field(default="", max_length=100)
    cursor: int = Field(default=0, ge=0, le=20000)
    scan_limit: int = Field(default=1, ge=1, le=10)
    seen_room_ids: List[str] = Field(default_factory=list, max_length=20000)

    @model_validator(mode="after")
    def validate_seen_room_ids(self):
        cleaned = []
        seen = set()
        for raw in self.seen_room_ids:
            value = str(raw or "").strip()
            if not value or len(value) > 128:
                raise ValueError("seen_room_ids contains an invalid room id")
            if value not in seen:
                seen.add(value)
                cleaned.append(value)
        self.seen_room_ids = cleaned
        return self


class TimetableCourseModel(BaseModel):
    id: str
    meeting_id: str = ""
    course_name: str
    course_code: str = ""
    teaching_class_id: str = ""
    weekday: int = Field(ge=0, le=7)
    start_section: int = Field(ge=0, le=30)
    end_section: int = Field(ge=0, le=30)
    start_time: str = ""
    end_time: str = ""
    teachers: List[str] = Field(default_factory=list)
    classes: List[str] = Field(default_factory=list)
    location: str = ""
    campus: str = ""
    course_nature: str = ""
    assessment_type: str = ""
    grading_scheme: str = ""
    cell_details: List[str] = Field(default_factory=list)
    title_details: List[str] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)
    preselected: bool = False
    color: str = "#2563eb"
    activity_type: str = "other"
    activity_type_label: str = "课程"
    weeks: List[int] = Field(default_factory=list)
    recurrence_unknown: bool = True


class TimetableOtherCourseModel(BaseModel):
    course_name: str
    course_code: str = ""
    details: List[str] = Field(default_factory=list)


class TimetableScheduleResponse(BaseModel):
    mode: TimetableMode
    term_code: str
    campus_code: str
    target_id: str = ""
    week: Optional[int] = None
    courses: List[TimetableCourseModel]
    unscheduled: List[TimetableOtherCourseModel]
    practices: List[TimetableOtherCourseModel]


class PersonalTimetableResponse(BaseModel):
    """One account-bound, full-term personal timetable cache variant."""

    term_code: str
    campuses: List[TimetableCampusModel]
    weeks: List[TimetableWeekModel]
    sections_by_campus: Dict[str, List[TimetableSectionModel]] = Field(default_factory=dict)
    courses: List[TimetableCourseModel]
    unscheduled: List[TimetableOtherCourseModel]
    practices: List[TimetableOtherCourseModel]
    source: str = "local"
    is_fresh: bool
    last_update: datetime
    cache: Dict[str, Any]


class TimetableBootstrapResponse(BaseModel):
    """Cache-only startup payload; it never waits for the official service."""

    terms: List[TimetableTermModel] = Field(default_factory=list)
    current: Optional[str] = None
    index_cache: Dict[str, Any] = Field(default_factory=dict)
    personal: List[PersonalTimetableResponse] = Field(default_factory=list)


class TimetableSyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    term_code: Optional[str] = Field(default=None, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")
    include_next: bool = True
    force: bool = False


class TimetableSyncJob(BaseModel):
    resource: str
    variant: str
    status: str
    job_id: Optional[str] = None
    revision: Optional[str] = None


class TimetableSyncResponse(BaseModel):
    jobs: List[TimetableSyncJob] = Field(default_factory=list)
