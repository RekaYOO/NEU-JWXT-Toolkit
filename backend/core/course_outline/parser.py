"""Defensive parsers for the Emap course-outline application."""

from __future__ import annotations

import re
from typing import Any, Iterable


COURSE_CODE_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

def normalize_course_code(value: str) -> str:
    code = str(value or "").strip()
    if not COURSE_CODE_RE.fullmatch(code):
        raise ValueError("invalid course code")
    return code


def extract_rows(payload: Any, action: str | None = None) -> list[dict[str, Any]]:
    """Accept top-level rows and the common ``datas.<action>.rows`` shapes."""
    if not isinstance(payload, dict):
        return []
    candidates: list[Any] = [payload]
    datas = payload.get("datas")
    if isinstance(datas, dict):
        if action and isinstance(datas.get(action), dict):
            candidates.insert(0, datas[action])
        candidates.extend(value for value in datas.values() if isinstance(value, dict))
    for candidate in candidates:
        rows = candidate.get("rows") if isinstance(candidate, dict) else None
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
        if isinstance(rows, dict):
            return [rows]
    return []


def extract_container(payload: Any, action: str | None = None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    datas = payload.get("datas")
    if isinstance(datas, dict):
        if action and isinstance(datas.get(action), dict):
            return datas[action]
        for value in datas.values():
            if isinstance(value, dict) and ("rows" in value or "totalSize" in value):
                return value
    return payload


def display_value(row: dict[str, Any], field: str, *aliases: str) -> str:
    for key in (f"{field}_DISPLAY", f"{field}DISPLAY", *aliases, field):
        value = row.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def first_row(payloads: Iterable[tuple[Any, str | None]]) -> dict[str, Any]:
    for payload, action in payloads:
        rows = extract_rows(payload, action)
        if rows:
            return rows[0]
    return {}


def collect_dictionary_options(payload: Any, field_names: set[str]) -> dict[str, list[dict[str, str]]]:
    result = {field: [] for field in field_names}
    seen = {field: set() for field in field_names}

    def walk(value: Any, inherited_field: str = "") -> None:
        if isinstance(value, dict):
            field = str(value.get("name") or value.get("field") or inherited_field or "")
            if field in field_names:
                code = value.get("value", value.get("code", value.get("id")))
                label = value.get("label", value.get("name_DISPLAY", value.get("text")))
                if code not in (None, "") and label not in (None, ""):
                    key = str(code)
                    if key not in seen[field]:
                        seen[field].add(key)
                        result[field].append({"value": key, "label": str(label)})
            for nested in value.values():
                walk(nested, field if field in field_names else inherited_field)
        elif isinstance(value, list):
            for nested in value:
                walk(nested, inherited_field)

    walk(payload)
    return result


def extract_dictionary_options(payload: Any) -> list[dict[str, str]]:
    """Parse the several dictionary response shapes used by ``jwapp/code``."""
    found: list[dict[str, str]] = []
    seen: set[str] = set()
    code_keys = ("value", "code", "id", "DM", "dm", "key", "KEY")
    label_keys = ("label", "text", "name", "MC", "mc", "valueName", "display")

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            code = next((value.get(k) for k in code_keys if value.get(k) not in (None, "")), None)
            label = next((value.get(k) for k in label_keys if value.get(k) not in (None, "")), None)
            if code not in (None, "") and label not in (None, "") and not isinstance(label, (dict, list)):
                code = str(code).strip(); label = str(label).strip()
                if code not in seen:
                    seen.add(code); found.append({"value": code, "label": label})
            for nested in value.values():
                visit(nested)
        elif isinstance(value, list):
            for nested in value:
                visit(nested)
    visit(payload)
    return found


def present_rows(section: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Turn official acronym-heavy rows into a stable, user-facing structure."""
    titles = {
        "outline": "课程与教材信息", "objectives": "课程目标", "objective_support": "毕业要求支撑",
        "objective_content": "教学安排", "attainment": "课程目标达成标准", "grading_method": "成绩评定方法",
        "assessment_types": "考核形式", "quality_improvement": "质量评价与持续改进", "authors": "编制信息",
    }
    labels = {
        "outline": [("SYZY", "适用专业"), ("SFXYJC_DISPLAY", "选用教材"), ("XXKC", "先修课程"),
                    ("CKSJJXZY", "参考书及学习资源"), ("SYJC", "使用教材"), ("KCXZDM_DISPLAY", "课程性质"), ("QTSM", "其他说明")],
        "objectives": [("KCMB", "课程目标")],
        "objective_support": [("KCMB", "课程目标"), ("BYYQ", "毕业要求指标点"), ("CD_DISPLAY", "支撑强度"), ("QZ", "权重")],
        "objective_content": [("ZJ", "章节/知识模块"), ("JXNR", "教学内容"), ("JXYQ", "教学要求"), ("JXFF", "教学方法"),
                              ("DYKCMB", "对应课程目标"), ("KTJSXS", "课堂讲授学时"), ("SYXS", "实验学时"), ("KCSJXS", "课程设计学时"),
                              ("SJXS", "实践学时"), ("TLXS", "讨论学时"), ("SJIXS", "上机学时")],
        "attainment": [("DCQKPJDJ_DISPLAY", "达成等级"), ("KCMBDCQKPJBZ", "课程目标达成标准"), ("PJDJFS", "评定分数范围")],
        "grading_method": [("KCCJPDFF", "成绩评定方法")],
        "assessment_types": [("KHXSMC", "考核形式")],
        "quality_improvement": [("KCZLPJHGJJZ", "课程质量评价与持续改进机制")],
        "authors": [("ZBR", "执笔人"), ("SHR", "审核人"), ("PZR", "批准人"), ("BZRQ", "编制日期")],
    }
    if section == "assessment_weights":
        return {"kind": "table", "title": "考核占比", "columns": ["课程目标", "考核形式", "成绩占比"],
                "rows": [{"课程目标": str(r.get("KCMB") or ""), "考核形式": str(r.get("KHXSMC") or ""), "成绩占比": r.get("CJZB", "")}
                         for r in rows if any(r.get(k) not in (None, "") for k in ("KCMB", "KHXSMC", "CJZB"))]}
    if section == "assessment_links":
        return {"kind": "table", "title": "考核目标", "columns": ["课程目标"], "rows": [{"课程目标": r.get("KCMB", "")} for r in rows if r.get("KCMB") not in (None, "")]}
    if section == "attachments":
        return {"kind": "attachments", "title": "历史附件", "items": [{"name": r.get("FJ") or "历史附件"} for r in rows if r.get("FJ") not in (None, "")]}
    specs = labels.get(section)
    if not specs:
        return {"kind": "table", "title": section, "columns": [], "rows": []}
    items = []
    for row in rows:
        item = []
        for key, label in specs:
            value = display_value(row, key)
            if value not in (None, ""):
                item.append({"label": label, "value": value})
        if section == "objective_content":
            other_hours = [str(row.get(f"QTXS{index}")) for index in range(1, 9) if row.get(f"QTXS{index}") not in (None, "", 0, "0")]
            if other_hours:
                item.append({"label": "其他学时", "value": " / ".join(other_hours)})
        if item:
            items.append(item)
    return {"kind": "cards" if len(items) > 1 else "info", "title": titles.get(section, section), "items": items}
