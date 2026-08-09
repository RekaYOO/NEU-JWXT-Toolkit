"""Normalize official and human-readable teaching-week expressions."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any


_RANGE = re.compile(r"(\d{1,2})\s*(?:-|~|～|—|至)\s*(\d{1,2})")
_NUMBER = re.compile(r"(?<!\d)(\d{1,2})(?!\d)")
_EXCLUDED = re.compile(r"(?:除|不含)\s*第?\s*(\d{1,2})\s*周?")
_WEEK_CLAUSE = re.compile(
    r"(?:第\s*)?\d{1,2}"
    r"(?:\s*(?:-|~|～|—|至|,|，|、)\s*\d{1,2})*\s*周"
    r"(?:\s*[（(]?\s*[单双]\s*(?:周)?\s*[）)]?)?"
)


def _bounded_week(value: Any, max_week: int) -> int | None:
    try:
        week = int(value)
    except (TypeError, ValueError):
        return None
    return week if 1 <= week <= max_week else None


def _week_values(value: Any, max_week: int, strict_mixed_text: bool = False) -> set[int]:
    if value is None or isinstance(value, bool):
        return set()
    if isinstance(value, (int, float)):
        week = _bounded_week(value, max_week)
        return {week} if week is not None else set()
    if isinstance(value, Mapping):
        result: set[int] = set()
        for key in (
            "weeks", "weekList", "week_list", "week", "weekName",
            "weekText", "text", "name",
        ):
            if key in value:
                result.update(_week_values(value[key], max_week, strict_mixed_text))
        return result
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes)):
        result: set[int] = set()
        for item in value:
            result.update(_week_values(item, max_week, strict_mixed_text))
        return result

    text = str(value).strip()
    if strict_mixed_text:
        return _strict_week_values(text, max_week)
    if not text:
        return set()
    if "/" in text:
        week_parts = [part for part in text.split("/") if "周" in part]
        if week_parts:
            text = ",".join(week_parts)
    excluded = {
        week
        for raw in _EXCLUDED.findall(text)
        if (week := _bounded_week(raw, max_week)) is not None
    }
    result: set[int] = set()
    range_spans: list[tuple[int, int]] = []
    for match in _RANGE.finditer(text):
        range_spans.append(match.span())
        start = _bounded_week(match.group(1), max_week)
        end = _bounded_week(match.group(2), max_week)
        if start is None or end is None or start > end:
            continue
        result.update(range(start, end + 1))

    remainder = list(text)
    for start, end in range_spans:
        remainder[start:end] = " " * (end - start)
    for raw in _NUMBER.findall("".join(remainder)):
        week = _bounded_week(raw, max_week)
        if week is not None:
            result.add(week)

    odd = bool(re.search(r"(?:单周|[（(]\s*单\s*[）)])", text))
    even = bool(re.search(r"(?:双周|[（(]\s*双\s*[）)])", text))
    if odd and not even:
        result = {week for week in result if week % 2 == 1}
    elif even and not odd:
        result = {week for week in result if week % 2 == 0}
    return result - excluded


def _strict_week_values(value: str, max_week: int) -> set[int]:
    """Parse each explicit week clause independently, then apply exclusions."""

    result: set[int] = set()
    for match in _WEEK_CLAUSE.finditer(value):
        result.update(_week_values(match.group(0), max_week, False))
    excluded = {
        week
        for raw in _EXCLUDED.findall(value)
        if (week := _bounded_week(raw, max_week)) is not None
    }
    return result - excluded


def parse_weeks(
    value: Any,
    *,
    max_week: int = 30,
    strict_mixed_text: bool = False,
) -> tuple[int, ...]:
    """Return a unique, sorted and bounded teaching-week tuple."""

    if not 1 <= max_week <= 60:
        raise ValueError("max_week must be between 1 and 60")
    return tuple(sorted(_week_values(value, max_week, strict_mixed_text)))
