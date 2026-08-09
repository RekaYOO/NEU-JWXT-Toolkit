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


class TimetableTargetFilterOption(BaseModel):
    value: str
    label: str


class TimetableTargetFilterOptionsResponse(BaseModel):
    options: Dict[str, List[TimetableTargetFilterOption]] = Field(default_factory=dict)
    relations: List[Dict[str, str]] = Field(default_factory=list)


class TimetableScheduleRequest(TimetableContextRequest):
    campus_code: str = Field(min_length=1, max_length=64)
    week: Optional[int] = Field(default=None, ge=1, le=30)


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
