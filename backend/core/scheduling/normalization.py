"""Adapters from legacy timetable-shaped dictionaries to scheduling models."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any

from .models import ScheduleMeeting
from .weeks import parse_weeks


ACTIVITY_LABELS = {
    "lecture": "理论课",
    "experiment": "实验课",
    "practice": "实践课",
    "physical": "体育课",
    "other": "课程",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _integer(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _detail_texts(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [
        text
        for item in value
        if (text := _text(item.get("text") if isinstance(item, Mapping) else item))
    ]


def normalize_activity_type(value: Any, *, tags: Any = None) -> tuple[str, str]:
    raw = _text(value).lower()
    text = " ".join([raw, *_detail_texts(tags)]).lower()
    aliases = {
        "lecture": ("理论", "讲授", "lecture"),
        "experiment": ("实验", "experiment", "lab"),
        "practice": ("实践", "实习", "课程设计", "practice"),
        "physical": ("体育", "physical"),
    }
    activity_type = next(
        (key for key, keywords in aliases.items() if any(word in text for word in keywords)),
        raw if raw in ACTIVITY_LABELS else "other",
    )
    return activity_type, ACTIVITY_LABELS[activity_type]


def _week_source(payload: Mapping[str, Any]) -> tuple[Any, bool]:
    for key in (
        "weeks", "week_list", "weekList", "week_text", "weekText",
        "classWeeks", "week",
    ):
        if payload.get(key) not in (None, "", []):
            return payload[key], False
    for key in ("weeks_and_teachers", "weeksAndTeachers"):
        if payload.get(key) not in (None, "", []):
            return payload[key], True
    details = [
        *_detail_texts(payload.get("cell_details") or payload.get("cellDetail")),
        *_detail_texts(payload.get("title_details") or payload.get("titleDetail")),
    ]
    return (
        next((line for line in details if re.search(r"\d\s*(?:[-~～—至,，、]\s*\d)?\s*周", line)), None),
        True,
    )


def _weekday(value: Any) -> int:
    numeric = _integer(value)
    if 1 <= numeric <= 7:
        return numeric
    text = _text(value)
    chinese = "一二三四五六日"
    match = re.search(r"(?:星期|周)\s*([一二三四五六日天])", text)
    if not match:
        return 0
    day = "日" if match.group(1) == "天" else match.group(1)
    return chinese.index(day) + 1


def _section_range(payload: Mapping[str, Any]) -> tuple[int, int]:
    start = _integer(payload.get("start_section") or payload.get("beginSection"))
    end = _integer(payload.get("end_section") or payload.get("endSection"))
    if start or end:
        return start, end
    text = _text(payload.get("time") or payload.get("classSessions"))
    match = re.search(r"第?\s*(\d{1,2})\s*(?:-|~|～|—|至)\s*(\d{1,2})\s*节", text)
    if match:
        return int(match.group(1)), int(match.group(2))
    single = re.search(r"第?\s*(\d{1,2})\s*节", text)
    if single:
        number = int(single.group(1))
        return number, number
    return 0, 0


def _clock_range(payload: Mapping[str, Any]) -> tuple[str, str]:
    start = _text(payload.get("start_time") or payload.get("beginTime"))
    end = _text(payload.get("end_time") or payload.get("endTime"))
    if start or end:
        return start, end
    match = re.search(
        r"((?:[01]?\d|2[0-3]):[0-5]\d)\s*(?:-|~|～|—|至)\s*"
        r"((?:[01]?\d|2[0-3]):[0-5]\d)",
        _text(payload.get("time") or payload.get("classSessions")),
    )
    return (match.group(1), match.group(2)) if match else ("", "")


def normalize_meeting(
    payload: Mapping[str, Any],
    *,
    term_code: str,
    default_week: int | None = None,
    default_source: str = "timetable",
) -> ScheduleMeeting:
    """Normalize a meeting while retaining legacy identifiers as source IDs."""

    source = _text(payload.get("source")) or default_source
    source_id = _text(
        payload.get("source_id")
        or payload.get("candidate_id")
        or payload.get("teaching_class_id")
        or payload.get("teachingClassId")
        or payload.get("id")
    )
    course_name = _text(payload.get("course_name") or payload.get("courseName")) or "未命名课程"
    course_code = _text(payload.get("course_code") or payload.get("courseCode"))
    teaching_class_id = _text(
        payload.get("teaching_class_id") or payload.get("teachingClassId")
    )
    week_source, strict_mixed_text = _week_source(payload)
    weeks = parse_weeks(week_source, strict_mixed_text=strict_mixed_text)
    if not weeks and default_week is not None and 1 <= default_week <= 30:
        weeks = (default_week,)
    activity_type, activity_type_label = normalize_activity_type(
        payload.get("activity_type")
        or payload.get("course_type")
        or payload.get("courseType"),
        tags=payload.get("tags"),
    )
    start_section, end_section = _section_range(payload)
    start_time, end_time = _clock_range(payload)
    stable_shape = {
        "source": source,
        "source_id": source_id,
        "term_code": term_code,
        "course_name": course_name,
        "course_code": course_code,
        "teaching_class_id": teaching_class_id,
        "weeks": weeks,
        "weekday": _weekday(payload.get("weekday") or payload.get("dayOfWeek") or payload.get("day") or payload.get("classDays")),
        "start_section": start_section,
        "end_section": end_section,
        "start_time": start_time,
        "end_time": end_time,
        "location": _text(payload.get("location") or payload.get("place")),
        "campus": _text(payload.get("campus") or payload.get("campusName")),
    }
    digest = hashlib.sha256(
        json.dumps(stable_shape, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:24]
    meeting_id = _text(payload.get("meeting_id")) or f"mtg_{digest}"
    return ScheduleMeeting(
        meeting_id=meeting_id,
        activity_type=activity_type,
        activity_type_label=activity_type_label,
        recurrence_unknown=not bool(weeks),
        **stable_shape,
    )


def meeting_extension(meeting: ScheduleMeeting) -> dict[str, Any]:
    """Fields that can be added to the legacy timetable response safely."""

    return {
        "meeting_id": meeting.meeting_id,
        "activity_type": meeting.activity_type,
        "activity_type_label": meeting.activity_type_label,
        "weeks": list(meeting.weeks),
        "recurrence_unknown": meeting.recurrence_unknown,
    }
