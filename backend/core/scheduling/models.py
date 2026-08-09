"""Stable scheduling value objects shared by timetable-like features."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ConflictStatus(str, Enum):
    CONFLICT = "conflict"
    CLEAR = "clear"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ScheduleMeeting:
    meeting_id: str
    source: str
    source_id: str
    term_code: str
    course_name: str
    course_code: str = ""
    teaching_class_id: str = ""
    activity_type: str = "other"
    activity_type_label: str = "课程"
    weeks: tuple[int, ...] = field(default_factory=tuple)
    recurrence_unknown: bool = True
    weekday: int = 0
    start_section: int = 0
    end_section: int = 0
    start_time: str = ""
    end_time: str = ""
    location: str = ""
    campus: str = ""


@dataclass(frozen=True)
class ConflictMatch:
    baseline_meeting_id: str
    baseline_course_name: str
    status: ConflictStatus
    reason: str
    overlapping_weeks: tuple[int, ...] = field(default_factory=tuple)
    weekday: int = 0
    start_section: int = 0
    end_section: int = 0


@dataclass(frozen=True)
class CandidateConflictResult:
    candidate_id: str
    candidate_meeting_id: str
    status: ConflictStatus
    matches: tuple[ConflictMatch, ...] = field(default_factory=tuple)
