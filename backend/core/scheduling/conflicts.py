"""Deterministic conflict detection over normalized schedule meetings."""

from __future__ import annotations

import re
from collections.abc import Iterable

from .models import (
    CandidateConflictResult,
    ConflictMatch,
    ConflictStatus,
    ScheduleMeeting,
)


_CLOCK = re.compile(r"^(\d{1,2}):(\d{2})$")


def _normalized_course_name(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()


def same_course(baseline: ScheduleMeeting, candidate: ScheduleMeeting) -> bool:
    """Match course identity without conflating similarly named courses.

    A shared official course code is authoritative.  Exact normalized names
    are used only when either side lacks a course code, because older timetable
    payloads do not always expose it.
    """
    baseline_code = baseline.course_code.strip().casefold()
    candidate_code = candidate.course_code.strip().casefold()
    if baseline_code and candidate_code:
        return baseline_code == candidate_code
    baseline_name = _normalized_course_name(baseline.course_name)
    candidate_name = _normalized_course_name(candidate.course_name)
    return bool(baseline_name and baseline_name == candidate_name)


def _minutes(value: str) -> int | None:
    match = _CLOCK.fullmatch(value.strip())
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour * 60 + minute


def compare_meetings(
    baseline: ScheduleMeeting,
    candidate: ScheduleMeeting,
) -> ConflictMatch:
    """Compare two meetings without treating incomplete data as clear."""

    if baseline.term_code != candidate.term_code:
        return ConflictMatch(
            baseline.meeting_id, baseline.course_name, ConflictStatus.CLEAR, "term_disjoint"
        )

    if baseline.weeks and candidate.weeks:
        overlapping_weeks = tuple(sorted(set(baseline.weeks) & set(candidate.weeks)))
        if not overlapping_weeks:
            return ConflictMatch(
                baseline.meeting_id, baseline.course_name, ConflictStatus.CLEAR, "week_disjoint"
            )
        unknown_reason = ""
    else:
        overlapping_weeks = ()
        unknown_reason = "unknown_weeks"

    if baseline.weekday and candidate.weekday:
        if baseline.weekday != candidate.weekday:
            return ConflictMatch(
                baseline.meeting_id, baseline.course_name, ConflictStatus.CLEAR, "weekday_disjoint"
            )
    else:
        unknown_reason = unknown_reason or "unknown_weekday"

    section_pairs_valid = (
        1 <= baseline.start_section <= baseline.end_section
        and 1 <= candidate.start_section <= candidate.end_section
    )
    if section_pairs_valid:
        if (
            baseline.end_section < candidate.start_section
            or candidate.end_section < baseline.start_section
        ):
            return ConflictMatch(
                baseline.meeting_id, baseline.course_name, ConflictStatus.CLEAR, "section_disjoint"
            )
        overlap_reason = "section_overlap"
    else:
        baseline_start, baseline_end = _minutes(baseline.start_time), _minutes(baseline.end_time)
        candidate_start, candidate_end = _minutes(candidate.start_time), _minutes(candidate.end_time)
        times_valid = (
            baseline_start is not None
            and baseline_end is not None
            and candidate_start is not None
            and candidate_end is not None
            and baseline_start < baseline_end
            and candidate_start < candidate_end
        )
        if not times_valid:
            unknown_reason = unknown_reason or "unknown_time"
            overlap_reason = ""
        elif baseline_end <= candidate_start or candidate_end <= baseline_start:
            return ConflictMatch(
                baseline.meeting_id, baseline.course_name, ConflictStatus.CLEAR, "time_disjoint"
            )
        else:
            overlap_reason = "time_overlap"

    status = ConflictStatus.UNKNOWN if unknown_reason else ConflictStatus.CONFLICT
    return ConflictMatch(
        baseline_meeting_id=baseline.meeting_id,
        baseline_course_name=baseline.course_name,
        status=status,
        reason=unknown_reason or overlap_reason,
        overlapping_weeks=overlapping_weeks,
        weekday=baseline.weekday or candidate.weekday,
        start_section=baseline.start_section,
        end_section=baseline.end_section,
    )


def check_conflicts(
    baseline: Iterable[ScheduleMeeting],
    candidates: Iterable[ScheduleMeeting],
    *,
    ignore_same_course: bool = False,
) -> tuple[CandidateConflictResult, ...]:
    """Evaluate candidates in order; conflict dominates unknown, then clear."""

    baseline_rows = tuple(baseline)
    results: list[CandidateConflictResult] = []
    for candidate in candidates:
        sections_known = 1 <= candidate.start_section <= candidate.end_section
        candidate_start = _minutes(candidate.start_time)
        candidate_end = _minutes(candidate.end_time)
        clock_known = (
            candidate_start is not None
            and candidate_end is not None
            and candidate_start < candidate_end
        )
        candidate_incomplete = (
            not candidate.weeks
            or not candidate.weekday
            or not (sections_known or clock_known)
        )
        comparisons = tuple(
            compare_meetings(row, candidate)
            for row in baseline_rows
            if not (ignore_same_course and same_course(row, candidate))
        )
        relevant = tuple(
            comparison
            for comparison in comparisons
            if comparison.status is not ConflictStatus.CLEAR
        )
        if any(item.status is ConflictStatus.CONFLICT for item in relevant):
            status = ConflictStatus.CONFLICT
        elif candidate_incomplete or any(
            item.status is ConflictStatus.UNKNOWN for item in relevant
        ):
            status = ConflictStatus.UNKNOWN
        else:
            status = ConflictStatus.CLEAR
        results.append(
            CandidateConflictResult(
                candidate_id=candidate.source_id or candidate.meeting_id,
                candidate_meeting_id=candidate.meeting_id,
                status=status,
                matches=relevant,
            )
        )
    return tuple(results)
