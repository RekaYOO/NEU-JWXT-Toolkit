"""Canonical payload adapters for the first project cache resources."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import asdict, is_dataclass
from typing import Any, Iterable, Mapping

from backend.core.academic.report import AcademicReportAPI
from backend.core.academic.research_training import ResearchTrainingAPI
from backend.core.festival_activities import fetch_festival_activities as _fetch_festival


SCORE_TRACKED_FIELDS = (
    "name",
    "code",
    "score",
    "gpa",
    "credit",
    "term",
    "term_display",
    "course_type",
    "course_category",
    "general_category",
    "exam_type",
    "exam_status",
    "course_nature",
    "is_passed",
)
SCORE_CACHE_FIELDS = (*SCORE_TRACKED_FIELDS, "detail_ref")
# Backward-compatible name used by tracking and GPA reconciliation.
SCORE_FIELDS = SCORE_TRACKED_FIELDS


def _plain(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def score_key(score: Mapping[str, Any]) -> str:
    """Stable course identity used by cache diff and GPA reconciliation."""
    code = str(score.get("code") or "")
    term = str(score.get("term") or "")
    if code:
        return "\x1f".join((code, term))
    # Malformed legacy rows must not collapse into one dictionary key. The
    # fallback intentionally excludes grade fields so a grade change remains a
    # modification of the same course.
    fallback = {
        "term": term,
        "name": str(score.get("name") or ""),
        "credit": score.get("credit"),
        "course_type": str(score.get("course_type") or ""),
    }
    digest = hashlib.sha256(
        json.dumps(
            fallback, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    return f"legacy:{digest}"


def score_to_dict(score: Any) -> dict[str, Any]:
    source = _plain(score)
    if not isinstance(source, Mapping):
        raise TypeError("score payload must be mapping-like")
    return {field: source.get(field) for field in SCORE_CACHE_FIELDS}


def score_detail_variant(course_code: str, term: str) -> str:
    """Opaque cache variant for one course; never contains the remote WID."""
    encoded = f"{course_code}\x1f{term}".encode("utf-8")
    return "course-" + hashlib.sha256(encoded).hexdigest()[:32]


def canonicalize_score_detail(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise TypeError("score detail payload must be an object")
    items = []
    for item in payload.get("item_scores") or []:
        if not isinstance(item, Mapping):
            continue
        items.append({
            "code": str(item.get("code") or ""),
            "name": str(item.get("name") or ""),
            "value": item.get("value"),
            "pass": item.get("pass") if isinstance(item.get("pass"), bool) else None,
            "highest_score_in_proportion": bool(
                item.get("highest_score_in_proportion")
            ),
        })
    return {
        "course_code": str(payload.get("course_code") or ""),
        "term": str(payload.get("term") or ""),
        "source_score": str(payload.get("source_score") or ""),
        "source_gpa": payload.get("source_gpa"),
        "score": str(payload.get("score") or ""),
        "grade_point": str(payload.get("grade_point") or ""),
        "pass": payload.get("pass") if isinstance(payload.get("pass"), bool) else None,
        "item_scores": items,
    }


def diff_score_detail(previous: Any, current: Any) -> dict[str, Any]:
    return {
        "detail_changed": previous != current,
        "item_count": len((current or {}).get("item_scores") or []),
    }


def fetch_scores(auth: Any) -> dict[str, Any]:
    scores = auth.academic.get_scores()
    overall_gpa = auth.academic.get_overall_gpa()
    return {
        "scores": [score_to_dict(score) for score in scores],
        "overall_gpa": overall_gpa,
    }


def canonicalize_scores(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise TypeError("scores payload must be an object")
    scores = [score_to_dict(score) for score in payload.get("scores") or []]
    scores.sort(key=lambda score: (score_key(score), repr(sorted(score.items()))))
    return {
        "scores": scores,
        "overall_gpa": payload.get("overall_gpa"),
    }


def diff_scores(previous: Any, current: Any) -> dict[str, Any]:
    def tracked(score: Mapping[str, Any]) -> dict[str, Any]:
        return {field: score.get(field) for field in SCORE_TRACKED_FIELDS}

    old_scores = {
        score_key(score): tracked(score)
        for score in (previous or {}).get("scores", [])
        if isinstance(score, Mapping)
    }
    new_scores = {
        score_key(score): tracked(score)
        for score in (current or {}).get("scores", [])
        if isinstance(score, Mapping)
    }
    added = [new_scores[key] for key in sorted(new_scores.keys() - old_scores.keys())]
    removed = [old_scores[key] for key in sorted(old_scores.keys() - new_scores.keys())]
    changed = [
        {"before": old_scores[key], "after": new_scores[key]}
        for key in sorted(old_scores.keys() & new_scores.keys())
        if old_scores[key] != new_scores[key]
    ]
    overall_before = (previous or {}).get("overall_gpa")
    overall_after = (current or {}).get("overall_gpa")
    return {
        "added": added,
        "removed": removed,
        "changed": changed,
        "overall_gpa_changed": overall_before != overall_after,
        "counts": {
            "added": len(added),
            "removed": len(removed),
            "changed": len(changed),
        },
    }


def fetch_academic_report(auth: Any, converter: Any) -> dict[str, Any]:
    report = AcademicReportAPI(auth).get_report()
    if report is None:
        raise RuntimeError("获取培养计划失败")
    return converter(report)


def canonicalize_academic_report(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise TypeError("academic report payload must be an object")
    result = _plain(payload)

    def number(value: Any) -> Any:
        # JSON distinguishes 4 and 4.0 even though they have the same
        # academic meaning.  Normalize calculated credit values so harmless
        # serializer changes do not manufacture a new cache revision.
        if isinstance(value, float):
            value = round(value, 6)
            return int(value) if value.is_integer() else value
        return value

    def normalize(value: Any, *, field: str = "") -> Any:
        if isinstance(value, Mapping):
            return {
                str(key): normalize(item, field=str(key))
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [normalize(item) for item in value]
        if isinstance(value, tuple):
            return [normalize(item) for item in value]
        return number(value)

    result = normalize(result)
    # This is the local calculation timestamp, not academic content. Including it
    # would manufacture a new revision on every successful check.
    result["calculated_time"] = ""
    return result


def academic_report_revision_payload(payload: Any) -> dict[str, Any]:
    """Build an order-insensitive projection without changing display order."""
    result = canonicalize_academic_report(payload)

    def category_key(category: Mapping[str, Any]) -> tuple[str, ...]:
        return (
            str(category.get("wid") or ""),
            str(category.get("course_group_wid") or ""),
            str(category.get("category_code") or ""),
            str(category.get("path") or ""),
            str(category.get("name") or ""),
        )

    def course_key(course: Mapping[str, Any]) -> tuple[str, ...]:
        return (
            str(course.get("course_code") or ""),
            str(course.get("course_name") or ""),
            str(course.get("term_code") or ""),
        )

    def normalize_category(category: Mapping[str, Any]) -> dict[str, Any]:
        normalized = dict(category)
        normalized["courses"] = sorted(
            (dict(course) for course in category.get("courses") or []),
            key=course_key,
        )
        normalized["children"] = sorted(
            (
                normalize_category(child)
                for child in category.get("children") or []
                if isinstance(child, Mapping)
            ),
            key=category_key,
        )
        return normalized

    result["categories"] = sorted(
        (
            normalize_category(category)
            for category in result.get("categories") or []
            if isinstance(category, Mapping)
        ),
        key=category_key,
    )
    result["outside_courses"] = sorted(
        (
            dict(course)
            for course in result.get("outside_courses") or []
            if isinstance(course, Mapping)
        ),
        key=course_key,
    )
    return result


def diff_academic_report(previous: Any, current: Any) -> dict[str, Any]:
    if previous is None:
        return {"initial": True}
    # Compare the same semantic representation used for revisions.  This also
    # makes old caches tolerant of a changed remote ordering.
    previous = academic_report_revision_payload(previous)
    current = academic_report_revision_payload(current)
    before = (previous or {}).get("credit_summary") or {}
    after = (current or {}).get("credit_summary") or {}
    return {
        "credit_summary_changed": before != after,
        "category_tree_changed": (previous or {}).get("categories")
        != (current or {}).get("categories"),
        "outside_courses_changed": (previous or {}).get("outside_courses")
        != (current or {}).get("outside_courses"),
    }


def _cacheable_topic(topic: Mapping[str, Any]) -> dict[str, Any]:
    return {
        str(key): _plain(value)
        for key, value in topic.items()
        if key
        not in {
            "contact",
            "advisor_contact",
            "advisor_email",
            "advisor_phone",
            "phone",
            "mobile",
            "email",
            "application_reason",
            "rejection_reason",
        }
    }


def fetch_research_training(auth: Any) -> dict[str, Any]:
    api = ResearchTrainingAPI(auth)
    batch = api.get_current_batch()
    eligibility = api.get_eligibility(batch.batch_id)
    return {
        "batch": _plain(batch),
        "eligibility": _plain(eligibility),
        "topics": [
            _cacheable_topic(topic)
            for topic in api.get_all_topics(batch.batch_id)
        ],
        "confirmed_topics": [
            _cacheable_topic(topic)
            for topic in api.get_confirmed_topics(batch.batch_id)
            if isinstance(topic, Mapping)
        ],
    }


def _topic_id(topic: Mapping[str, Any]) -> str:
    return str(
        topic.get("topic_id")
        or topic.get("id")
        or topic.get("project_id")
        or ""
    )


def canonicalize_research_training(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise TypeError("research training payload must be an object")
    topics = [
        _cacheable_topic(topic)
        for topic in payload.get("topics") or []
        if isinstance(topic, Mapping)
    ]
    topics.sort(key=lambda topic: (_topic_id(topic), repr(sorted(topic.items()))))
    confirmed = [
        _cacheable_topic(topic)
        for topic in payload.get("confirmed_topics") or []
        if isinstance(topic, Mapping)
    ]
    confirmed.sort(key=lambda topic: (_topic_id(topic), repr(sorted(topic.items()))))
    return {
        "batch": _plain(payload.get("batch") or {}),
        "eligibility": _plain(payload.get("eligibility") or {}),
        "topics": topics,
        "confirmed_topics": confirmed,
    }


def diff_research_training(previous: Any, current: Any) -> dict[str, Any]:
    old_topics = {
        _topic_id(topic): topic
        for topic in (previous or {}).get("topics", [])
        if isinstance(topic, Mapping)
    }
    new_topics = {
        _topic_id(topic): topic
        for topic in (current or {}).get("topics", [])
        if isinstance(topic, Mapping)
    }
    changed_ids = sorted(
        key
        for key in old_topics.keys() & new_topics.keys()
        if old_topics[key] != new_topics[key]
    )
    return {
        "added_topic_ids": sorted(new_topics.keys() - old_topics.keys()),
        "removed_topic_ids": sorted(old_topics.keys() - new_topics.keys()),
        "changed_topic_ids": changed_ids,
        "batch_changed": (previous or {}).get("batch") != (current or {}).get("batch"),
        "eligibility_changed": (previous or {}).get("eligibility")
        != (current or {}).get("eligibility"),
        "confirmed_changed": (previous or {}).get("confirmed_topics")
        != (current or {}).get("confirmed_topics"),
    }


AVATAR_MAGIC = b"NEU-AVATAR\x01"


def avatar_payload(token: str, image: bytes) -> bytes:
    token_bytes = str(token).encode("utf-8")
    image_bytes = bytes(image)
    if not token_bytes or len(token_bytes) > 65535:
        raise ValueError("avatar token is missing or too long")
    if not image_bytes:
        raise ValueError("avatar image is empty")
    return (
        AVATAR_MAGIC
        + len(token_bytes).to_bytes(2, "big")
        + token_bytes
        + image_bytes
    )


def avatar_token(payload: bytes | bytearray | memoryview) -> str:
    raw = bytes(payload)
    if not raw.startswith(AVATAR_MAGIC) or len(raw) < len(AVATAR_MAGIC) + 2:
        raise ValueError("invalid avatar cache payload")
    offset = len(AVATAR_MAGIC)
    token_length = int.from_bytes(raw[offset:offset + 2], "big")
    token_start = offset + 2
    token_end = token_start + token_length
    if token_length < 1 or token_end >= len(raw):
        raise ValueError("invalid avatar token framing")
    return raw[token_start:token_end].decode("utf-8")


def avatar_bytes(payload: bytes | bytearray | memoryview) -> bytes:
    raw = bytes(payload)
    avatar_token(raw)
    offset = len(AVATAR_MAGIC)
    token_length = int.from_bytes(raw[offset:offset + 2], "big")
    return raw[offset + 2 + token_length:]


def canonicalize_avatar(payload: Any) -> bytes:
    if isinstance(payload, Mapping):
        # Transitional support for a development build that briefly used JSON.
        image = base64.b64decode(
            str(payload.get("image_base64") or ""), validate=True
        )
        payload = avatar_payload(str(payload.get("token") or ""), image)
    if not isinstance(payload, (bytes, bytearray, memoryview)):
        raise TypeError("avatar payload must be bytes-like")
    raw = bytes(payload)
    avatar_token(raw)
    avatar_bytes(raw)
    return raw


def diff_avatar(previous: Any, current: Any) -> dict[str, Any]:
    previous_token = avatar_token(previous) if previous else ""
    current_token = avatar_token(current)
    return {
        "token_changed": previous_token != current_token,
        "image_changed": bytes(previous or b"") != bytes(current),
    }


def fetch_festival_activities(auth: Any) -> dict[str, Any]:
    return _fetch_festival(auth)


def canonicalize_festival_activities(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise TypeError("festival activities payload must be an object")
    activities = [
        {str(key): _plain(value) for key, value in item.items()}
        for item in (payload.get("activities") or [])
        if isinstance(item, Mapping)
    ]
    activities.sort(key=lambda item: (
        str(item.get("section") or ""), str(item.get("id") or "")
    ))
    return {
        "activities": activities,
        "warnings": sorted(str(item) for item in (payload.get("warnings") or [])),
    }


def diff_festival_activities(previous: Any, current: Any) -> dict[str, Any]:
    old = {
        f"{item.get('section')}:{item.get('id')}": item
        for item in (previous or {}).get("activities", [])
    }
    new = {
        f"{item.get('section')}:{item.get('id')}": item
        for item in (current or {}).get("activities", [])
    }
    return {
        "added": len(new.keys() - old.keys()),
        "removed": len(old.keys() - new.keys()),
        "updated": sum(old[key] != new[key] for key in old.keys() & new.keys()),
    }


TIMETABLE_TERM_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


def personal_timetable_variant(term_code: str) -> str:
    """Return the controlled cache variant for one personal timetable term."""
    normalized = str(term_code or "").strip()
    if not TIMETABLE_TERM_PATTERN.fullmatch(normalized):
        raise ValueError("invalid timetable term code")
    return f"term:{normalized}"


def personal_timetable_term(variant: str) -> str:
    prefix = "term:"
    if not str(variant or "").startswith(prefix):
        raise ValueError("personal timetable cache requires a term variant")
    term_code = str(variant)[len(prefix):]
    personal_timetable_variant(term_code)
    return term_code


def canonicalize_personal_timetable(payload: Any) -> dict[str, Any]:
    """Keep only stable, user-visible timetable data in cache revisions."""
    if not isinstance(payload, Mapping):
        raise TypeError("personal timetable payload must be an object")

    def clean_rows(name: str, *, identity: tuple[str, ...]) -> list[dict[str, Any]]:
        rows = [
            _plain(row)
            for row in payload.get(name) or []
            if isinstance(row, Mapping)
        ]
        rows.sort(key=lambda row: tuple(str(row.get(key) or "") for key in identity))
        return rows

    sections_by_campus = {
        str(code): sorted(
            [_plain(item) for item in items or [] if isinstance(item, Mapping)],
            key=lambda item: int(item.get("number") or 0),
        )
        for code, items in sorted((payload.get("sections_by_campus") or {}).items())
        if isinstance(items, list)
    }
    return {
        "term_code": str(payload.get("term_code") or ""),
        "campuses": sorted(
            [
                _plain(row)
                for row in payload.get("campuses") or []
                if isinstance(row, Mapping)
            ],
            key=lambda row: (
                str(row.get("code") or "") != "all",
                str(row.get("code") or ""),
                str(row.get("name") or ""),
            ),
        ),
        "weeks": sorted(
            [_plain(row) for row in payload.get("weeks") or [] if isinstance(row, Mapping)],
            key=lambda row: int(row.get("number") or 0),
        ),
        "sections_by_campus": sections_by_campus,
        "courses": clean_rows(
            "courses",
            identity=("meeting_id", "id", "weekday", "start_section", "course_code"),
        ),
        "unscheduled": clean_rows("unscheduled", identity=("course_code", "course_name")),
        "practices": clean_rows("practices", identity=("course_code", "course_name")),
    }


def diff_personal_timetable(previous: Any, current: Any) -> dict[str, Any]:
    previous = previous or {}
    current = current or {}
    old_courses = {
        str(item.get("meeting_id") or item.get("id") or index): item
        for index, item in enumerate(previous.get("courses") or [])
    }
    new_courses = {
        str(item.get("meeting_id") or item.get("id") or index): item
        for index, item in enumerate(current.get("courses") or [])
    }
    return {
        "added_meeting_ids": sorted(new_courses.keys() - old_courses.keys()),
        "removed_meeting_ids": sorted(old_courses.keys() - new_courses.keys()),
        "changed_meeting_ids": sorted(
            key
            for key in old_courses.keys() & new_courses.keys()
            if old_courses[key] != new_courses[key]
        ),
        "context_changed": any(
            previous.get(key) != current.get(key)
            for key in ("campuses", "weeks", "sections_by_campus", "unscheduled", "practices")
        ),
    }
