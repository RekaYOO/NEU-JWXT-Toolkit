"""Typed HTTP contracts for cross-feature schedule conflict checks."""

from __future__ import annotations

from typing import Annotated, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


TermCode = str
ConflictStatusValue = Literal["conflict", "clear", "unknown"]
WeekNumber = Annotated[int, Field(ge=1, le=30)]
DetailText = Annotated[str, Field(max_length=500)]


class ScheduleMeetingInput(BaseModel):
    """Timetable-compatible input; new fields extend rather than replace legacy ones."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default="", max_length=128)
    meeting_id: str = Field(default="", max_length=128)
    candidate_id: str = Field(default="", max_length=128)
    source: str = Field(default="", max_length=32, pattern=r"^[A-Za-z0-9_-]*$")
    source_id: str = Field(default="", max_length=128)
    course_name: str = Field(min_length=1, max_length=200)
    course_code: str = Field(default="", max_length=128)
    teaching_class_id: str = Field(default="", max_length=128)
    activity_type: str = Field(default="", max_length=32)
    course_type: str = Field(default="", max_length=100)
    weeks: List[WeekNumber] = Field(default_factory=list, max_length=30)
    week_text: str = Field(default="", max_length=200)
    week: str = Field(default="", max_length=200)
    weekday: int = Field(default=0, ge=0, le=7)
    day: str = Field(default="", max_length=50)
    start_section: int = Field(default=0, ge=0, le=30)
    end_section: int = Field(default=0, ge=0, le=30)
    start_time: str = Field(
        default="", max_length=8, pattern=r"^(?:|(?:[01]?\d|2[0-3]):[0-5]\d)$"
    )
    end_time: str = Field(
        default="", max_length=8, pattern=r"^(?:|(?:[01]?\d|2[0-3]):[0-5]\d)$"
    )
    time: str = Field(default="", max_length=100)
    location: str = Field(default="", max_length=300)
    campus: str = Field(default="", max_length=100)
    tags: List[DetailText] = Field(default_factory=list, max_length=20)
    cell_details: List[DetailText] = Field(default_factory=list, max_length=30)
    title_details: List[DetailText] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def validate_ranges(self):
        if self.start_section and self.end_section and self.start_section > self.end_section:
            raise ValueError("start_section must not exceed end_section")
        if bool(self.start_time) != bool(self.end_time):
            raise ValueError("start_time and end_time must be provided together")
        return self


class ScheduleConflictBatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    term_code: TermCode = Field(
        min_length=1, max_length=32, pattern=r"^[A-Za-z0-9_-]+$"
    )
    week: Optional[int] = Field(default=None, ge=1, le=30)
    candidates: List[ScheduleMeetingInput] = Field(min_length=1, max_length=500)
    ignore_same_course: bool = False
    resolve_personal_timetable: bool = False


class ScheduleConflictMatchModel(BaseModel):
    baseline_meeting_id: str
    baseline_course_name: str
    baseline_course_code: str = ""
    baseline_weeks: List[int] = Field(default_factory=list)
    status: ConflictStatusValue
    reason: str
    overlapping_weeks: List[int] = Field(default_factory=list)
    weekday: int = Field(default=0, ge=0, le=7)
    start_section: int = Field(default=0, ge=0, le=30)
    end_section: int = Field(default=0, ge=0, le=30)


class ScheduleCandidateConflictModel(BaseModel):
    candidate_id: str
    candidate_meeting_id: str
    status: ConflictStatusValue
    matches: List[ScheduleConflictMatchModel] = Field(default_factory=list)


class ScheduleConflictBatchResponse(BaseModel):
    term_code: str
    week: Optional[int] = None
    baseline_count: int
    baseline_available: bool
    baseline_revision: Optional[str] = None
    baseline_stale: bool
    candidate_count: int
    results: List[ScheduleCandidateConflictModel]
