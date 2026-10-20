"""Bounded, value-safe evidence for background refreshes.

Only callers in this module choose fields from business payloads. Never pass a
remote body, exception message, course title or authentication object to emit().
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import traceback
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from functools import wraps
from typing import Any, Mapping

from .logger import LogCategory, get_logger


_context: ContextVar[dict | None] = ContextVar("sync_diagnostics", default=None)


def diagnostic(function):
    @wraps(function)
    def guarded(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except Exception as error:
            return {"diagnostic_error": type(error).__name__}
    return guarded


@contextmanager
def sync_context(**fields):
    token = _context.set(fields)
    try:
        yield
    finally:
        _context.reset(token)


def fingerprint(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def safe_number(value: Any):
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (ValueError, TypeError, OverflowError):
        return None


def safe_variant(value: str) -> str:
    if value == "default" or re.fullmatch(r"term:\d{4}-\d{4}-[12]", value):
        return value
    return "ref:" + fingerprint(value)[:16]


def emit(event: str, *, level=logging.INFO, **fields) -> None:
    # A full disk or a broken diagnostic handler must never change cache state.
    try:
        payload = {**(_context.get() or {}), "event": event, **fields}
        get_logger("refresh", LogCategory.SYNC).log(
            level, "SYNC %s", json.dumps(payload, ensure_ascii=False,
                                         separators=(",", ":")),
        )
    except Exception:
        pass


def failure_fields(error: BaseException) -> dict:
    # Stack locations are useful; exception text and source lines can hold PII.
    fields = {
        "error_type": type(error).__name__,
        "trace": [{"file": Path(frame.filename).name, "line": frame.lineno,
                   "function": frame.name}
                  for frame in traceback.extract_tb(error.__traceback__)[-12:]],
    }
    causes = []
    seen = {id(error)}
    cause = error.__cause__ or (None if error.__suppress_context__ else error.__context__)
    while cause is not None and id(cause) not in seen and len(causes) < 3:
        seen.add(id(cause))
        causes.append({"error_type": type(cause).__name__, "trace": [
            {"file": Path(frame.filename).name, "line": frame.lineno, "function": frame.name}
            for frame in traceback.extract_tb(cause.__traceback__)[-6:]]})
        cause = cause.__cause__ or (None if cause.__suppress_context__ else cause.__context__)
    if causes:
        fields["causes"] = causes
    return fields


@diagnostic
def report_source(data: Mapping) -> dict:
    root = data.get("fanbx") or {}
    nodes = []
    legacy = modern = 0

    def walk(items):
        nonlocal legacy, modern
        for item in items or []:
            if not isinstance(item, Mapping):
                continue
            nodes.append(item)
            legacy += len(item.get("data") or [])
            modern += len(item.get("checkCourseVOS") or [])
            walk(item.get("children"))

    walk(root.get("children"))
    required = safe_number(root.get("creditsRequired"))
    # The exact hash includes calculatedTime. The content hash does not; array
    # order still matters and is explicitly distinct from the cache revision.
    content = {key: value for key, value in data.items() if key != "calculatedTime"}
    return {
        "response_fingerprint": fingerprint(data),
        "content_fingerprint": fingerprint(content),
        "program_ref": fingerprint(root.get("educationalProgramCode"))[:16],
        "required_field_present": "creditsRequired" in root,
        "declared_required": required,
        "required_source": "fanbx.creditsRequired" if required else "top_category_sum",
        "top_category_count": len(root.get("children") or []),
        "category_count": len(nodes), "legacy_course_rows": legacy,
        "modern_course_rows": modern,
        "top_categories": [{
            "ref": fingerprint([node.get("id"), node.get("courseGroupWid"),
                                node.get("courseCategory"), node.get("name")])[:16],
            "required_present": "creditsRequired" in node,
            "required": safe_number(node.get("creditsRequired")),
        } for node in (root.get("children") or [])[:40] if isinstance(node, Mapping)],
    }


@diagnostic
def payload_stats(resource: str, payload: Any) -> dict:
    if not isinstance(payload, Mapping):
        return {"present": payload is not None}
    if resource == "academic-report":
        categories = courses = 0

        def walk(nodes):
            nonlocal categories, courses
            for node in nodes or []:
                categories += 1
                courses += len(node.get("courses") or [])
                walk(node.get("children"))

        walk(payload.get("categories"))
        summary = payload.get("credit_summary") or {}
        return {"category_count": categories, "course_count": courses,
                "outside_course_count": len(payload.get("outside_courses") or []),
                "credits": {key: safe_number(summary.get(key)) for key in (
                    "total_required", "total_earned", "total_passed", "total_selected",
                    "total_taken", "total_remaining")}}
    if resource == "personal-timetable":
        return {key + "_count": len(payload.get(key) or [])
                for key in ("courses", "unscheduled", "practices")}
    return {"present": True}


def changed_fields(previous: Any, current: Any) -> dict:
    """Report schema paths, never dynamic keys or before/after string values."""
    counts: dict[str, int] = {}
    allowed = {
        # Explicit schema allowlist: remote/dynamic dictionary keys stay opaque.
        "categories", "children", "courses", "outside_courses", "credit_summary",
        "total_required", "total_earned", "total_passed", "total_selected",
        "total_taken", "total_remaining", "required_credits", "earned_credits",
        "taken_credits", "remaining_credits", "declared_required_credits",
        "course_name", "course_code", "name", "id", "meeting_id", "color",
        "weeks", "sections", "day_of_week", "weekday", "start_section", "end_section",
        "location", "campus", "teachers", "classes", "term", "term_code",
        "unscheduled", "practices", "program_code", "program_name",
        "course_nature", "assessment_method", "preselected", "tags", "cell_details",
        "title_details", "status", "credit", "score", "is_passed", "path", "wid",
        "course_group_wid", "category_code", "calculated_time",
    }

    def walk(old, new, path="$"):
        if old == new:
            return
        if isinstance(old, Mapping) and isinstance(new, Mapping):
            for key in old.keys() | new.keys():
                segment = key if key in allowed else "<other>"
                walk(old.get(key), new.get(key), path + "." + segment)
        elif isinstance(old, list) and isinstance(new, list):
            if len(old) != len(new):
                counts[path + ".length"] = 1
            for before, after in zip(old, new):
                walk(before, after, path + "[]")
        else:
            counts[path] = counts.get(path, 0) + 1

    walk(previous, current)
    ordered = sorted(counts.items())
    return {"paths": dict(ordered[:80]), "truncated": len(ordered) > 80,
            "comparison": "positional_arrays"}


@diagnostic
def cache_evidence(resource: str, previous: Any, current: Any) -> dict:
    return {"before": payload_stats(resource, previous),
            "after": payload_stats(resource, current),
            "field_changes": changed_fields(previous, current)
            if resource in {"academic-report", "personal-timetable"} else {}}
