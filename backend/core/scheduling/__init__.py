"""Shared scheduling normalization and conflict detection domain."""

from .conflicts import check_conflicts, compare_meetings, same_course
from .models import (
    CandidateConflictResult,
    ConflictMatch,
    ConflictStatus,
    ScheduleMeeting,
)
from .normalization import meeting_extension, normalize_activity_type, normalize_meeting
from .weeks import parse_weeks

__all__ = [
    "CandidateConflictResult",
    "ConflictMatch",
    "ConflictStatus",
    "ScheduleMeeting",
    "check_conflicts",
    "compare_meetings",
    "same_course",
    "meeting_extension",
    "normalize_activity_type",
    "normalize_meeting",
    "parse_weeks",
]
