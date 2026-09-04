"""Shared JWXK round semantics.

The official result endpoints expose several feeds at the same time and can
include records from another round.  Keep the interpretation here so manual
pages, automation and notifications do not each invent a different rule.
"""

from __future__ import annotations

from typing import Any


SELECTION_MODES: dict[str, dict[str, Any]] = {
    "02": {
        "code": "02",
        "label": "抢选",
        "participant_field": "selected_count",
        "participant_label": "已选人数",
        "current_result_feeds": ("selected",),
        "task_types": ("selection", "vacancy_swap"),
    },
    "04": {
        "code": "04",
        "label": "权重",
        "participant_field": "weight_participant_count",
        "participant_label": "已投注人数",
        "current_result_feeds": ("volunteered",),
        "task_types": ("weight_strategy",),
    },
}

_NON_MUTATION_TYPES = {"", "ALL", "ROUND", "ALLKC"}


def selection_mode(selection_type_code: Any) -> dict[str, Any]:
    """Return a stable mode descriptor, including a conservative fallback."""

    code = str(selection_type_code or "")
    return SELECTION_MODES.get(code, {
        "code": code,
        "label": "选课",
        "participant_field": "selected_count",
        "participant_label": "已选人数",
        "current_result_feeds": ("selected",),
        "task_types": (),
    })


def is_real_teaching_class_type(value: Any) -> bool:
    return str(value or "").strip().upper() not in _NON_MUTATION_TYPES


def _record_value(record: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = str(record.get(key) or "").strip()
        if value:
            return value
    return ""


def classify_selection_record(
    record: dict[str, Any],
    *,
    selection_type_code: str,
    batch_code: str,
    term_code: str = "",
    record_type: str = "",
) -> dict[str, Any]:
    """Classify one official result row for the active round.

    Missing batch metadata is not treated as proof that a row belongs to
    another round because some JWXK deployments omit it.  The active endpoint,
    result feed and the 0/0 sentinel are then the strongest available evidence.
    Explicit mismatches always win.
    """

    mode = selection_mode(selection_type_code)
    feed = str(record_type or record.get("selection_record_type") or "").strip()
    record_batch = _record_value(
        record, "record_batch_code", "elective_batch_code",
        "electiveBatchCode", "batch_code", "batchCode",
    )
    record_term = _record_value(
        record, "record_term_code", "term_code", "termCode",
        "schoolTerm", "semester", "xnxq",
    )
    participant_field = str(mode["participant_field"])
    participants = record.get(participant_field)
    capacity = record.get("capacity")

    reason = ""
    current = True
    if feed and feed not in mode["current_result_feeds"]:
        current = False
        reason = "结果来源不属于当前轮次类型"
    elif record_batch and batch_code and record_batch != batch_code:
        current = False
        reason = "记录属于其他选课批次"
    elif record_term and term_code and record_term != term_code:
        current = False
        reason = "记录属于其他学期"
    else:
        try:
            zero_zero = float(participants) == 0 and float(capacity) == 0
        except (TypeError, ValueError):
            zero_zero = False
        if zero_zero:
            current = False
            reason = "人数和容量均为 0，属于其他轮次或不可操作记录"

    return {
        "current_batch_record": current,
        "operation_allowed": current,
        "operation_block_reason": "" if current else reason,
        "record_batch_code": record_batch,
        "record_term_code": record_term,
        "selection_record_type": feed,
    }


def current_selection_records(
    official: dict[str, Any],
    *,
    selection_type_code: str,
    batch_code: str,
    term_code: str = "",
) -> list[dict[str, Any]]:
    """Return only records authoritative for the active round and mode."""

    mode = selection_mode(selection_type_code)
    rows: list[dict[str, Any]] = []
    for feed in mode["current_result_feeds"]:
        for raw in official.get(feed) or []:
            if not isinstance(raw, dict):
                continue
            classified = classify_selection_record(
                raw,
                selection_type_code=selection_type_code,
                batch_code=batch_code,
                term_code=term_code,
                record_type=feed,
            )
            if classified["current_batch_record"]:
                rows.append({**raw, **classified})
    return rows


def annotate_selection_result(
    official: dict[str, Any],
    *,
    selection_type_code: str,
    batch_code: str,
    term_code: str = "",
) -> dict[str, Any]:
    result = dict(official)
    for feed in ("selected", "volunteered", "withdrawal"):
        result[feed] = [
            {
                **raw,
                **classify_selection_record(
                    raw,
                    selection_type_code=selection_type_code,
                    batch_code=batch_code,
                    term_code=term_code,
                    record_type=feed,
                ),
            }
            for raw in official.get(feed) or []
            if isinstance(raw, dict)
        ]
    return result
