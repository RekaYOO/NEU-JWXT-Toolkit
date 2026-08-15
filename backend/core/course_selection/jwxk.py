"""Adapter for NEU's independent JWXK course-selection system.

Public batch discovery and authenticated reads share the same session adapter.
Mutations are intentionally narrow: the server re-fetches the official class
token immediately before a user-confirmed select, bid, or withdrawal request.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import re
from typing import Any, Literal, TYPE_CHECKING
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

from backend.core.auth.client import NEULoginError
from backend.core.scheduling import parse_weeks

if TYPE_CHECKING:
    from backend.core.auth.client import NEUAuthClient


logger = logging.getLogger(__name__)

JWXK_ORIGIN = "https://jwxk.neu.edu.cn"
JWXK_PROFILE_URL = f"{JWXK_ORIGIN}/xsxk/profile/index.html"
JWXK_CAS_SERVICE = f"{JWXK_ORIGIN}/xsxk/auth/cas"
_BATCH_LIST_PATTERN = re.compile(
    r"loginVue\.batchList\s*=\s*(\[.*?\])\s*;",
    re.DOTALL,
)


class JwxkError(RuntimeError):
    """Raised when the official selection system cannot be interpreted."""


@dataclass(frozen=True)
class JwxkBatch:
    code: str
    name: str
    term_code: str
    term_name: str
    begin_time: str
    end_time: str
    selection_type: str
    selection_type_code: str
    tactic_name: str
    course_types: tuple[str, ...]
    need_confirm: bool
    notice: str
    state: Literal["not_started", "active", "ended", "unknown"]
    can_enter: bool
    account_selectable: bool = False
    confirmed: bool = False
    week_range: str = ""
    allow_conflict: bool = False
    allow_cross_campus: bool = False
    menus: tuple[dict[str, str], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["course_types"] = list(self.course_types)
        value["menus"] = list(self.menus)
        return value


def _parse_official_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _batch_state(begin: Any, end: Any, now: datetime) -> str:
    begin_at = _parse_official_time(begin)
    end_at = _parse_official_time(end)
    if begin_at is None or end_at is None:
        return "unknown"
    if now < begin_at:
        return "not_started"
    if now > end_at:
        return "ended"
    return "active"


def parse_public_batches(html: str, *, now: datetime | None = None) -> list[JwxkBatch]:
    match = _BATCH_LIST_PATTERN.search(html or "")
    if not match:
        raise JwxkError("选课系统首页未提供批次数据")
    try:
        rows = json.loads(match.group(1))
    except (TypeError, ValueError) as error:
        raise JwxkError("选课系统批次数据格式已变化") from error
    if not isinstance(rows, list):
        raise JwxkError("选课系统批次数据不是列表")
    captured_at = now or datetime.now()
    result: list[JwxkBatch] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        code = str(row.get("code") or "").strip()
        name = str(row.get("name") or "").strip()
        if not code or not name:
            continue
        state = _batch_state(row.get("beginTime"), row.get("endTime"), captured_at)
        result.append(JwxkBatch(
            code=code,
            name=name,
            term_code=str(row.get("schoolTerm") or "").strip(),
            term_name=str(row.get("schoolTermName") or "").strip(),
            begin_time=str(row.get("beginTime") or "").strip(),
            end_time=str(row.get("endTime") or "").strip(),
            selection_type=str(row.get("typeName") or "").strip(),
            selection_type_code=str(row.get("typeCode") or "").strip(),
            tactic_name=str(row.get("tacticName") or "").strip(),
            course_types=tuple(str(item) for item in row.get("clazzTypeList") or []),
            need_confirm=str(row.get("needConfirm") or "0") == "1",
            notice=str(row.get("confirmInfo") or "").strip(),
            state=state,
            can_enter=state == "active" and str(row.get("active") or "") == "1",
            allow_cross_campus=str(row.get("notRetakeMultiCampus") or "0") == "1",
        ))
    return result


def _official_datetime(value: Any) -> datetime | None:
    try:
        timestamp = int(value)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(
        timestamp / 1000,
        tz=timezone(timedelta(hours=8)),
    ).replace(tzinfo=None)


def parse_account_batches(rows: Any, *, official_now: Any) -> list[JwxkBatch]:
    """Normalize authenticated batch rows using the server's clock."""
    now = _official_datetime(official_now) or datetime.now()
    result: list[JwxkBatch] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        code = str(row.get("code") or "").strip()
        name = str(row.get("name") or "").strip()
        if not code or not name:
            continue
        state = _batch_state(row.get("beginTime"), row.get("endTime"), now)
        selectable = str(row.get("canSelect") or "0") == "1"
        menus = tuple(
            _course_type_menu(item)
            for item in row.get("menuList") or []
            if isinstance(item, dict) and item.get("teachingClassType")
        )
        result.append(JwxkBatch(
            code=code,
            name=name,
            term_code=str(row.get("schoolTerm") or "").strip(),
            term_name=str(row.get("schoolTermName") or "").strip(),
            begin_time=str(row.get("beginTime") or "").strip(),
            end_time=str(row.get("endTime") or "").strip(),
            selection_type=str(row.get("typeName") or "").strip(),
            selection_type_code=str(row.get("typeCode") or "").strip(),
            tactic_name=str(row.get("tacticName") or "").strip(),
            course_types=tuple(str(item) for item in row.get("clazzTypeList") or []),
            need_confirm=str(row.get("needConfirm") or "0") == "1",
            notice=str(row.get("confirmInfo") or "").strip(),
            state=state,
            can_enter=selectable and state == "active",
            account_selectable=selectable,
            confirmed=str(row.get("isConfirmed") or "0") == "1",
            week_range=str(row.get("weekRange") or "").strip(),
            allow_conflict=str(row.get("noCheckTimeConflict") or "0") == "1",
            allow_cross_campus=str(row.get("notRetakeMultiCampus") or "0") == "1",
            menus=menus,
        ))
    return result


_FILTER_FIELDS = {
    "SFCT", "SFYM", "SFYX", "KCXZ", "KCLB", "KKDW", "XGXKLB", "XGXKLBDM", "CXCKLX",
    "SKXQ", "KSJC", "JSJC", "ZXNJ", "ZXYX", "ZXZY", "FXNJ",
    "FXYX", "FXZY", "TJBJ",
}
_COURSE_TYPE_NAMES = {
    "TJKC": "任务推荐班课程",
    "FANKC": "培养方案内课",
    "FAWKC": "培养方案外课程",
    "XGKC": "通识选修课",
    "CXKC": "重修课程",
    "TYKC": "体育项目",
    "FXKC": "辅修课程",
    "ALLKC": "全校课程查询",
    "BYKC": "本研课程",
    "ZYNKC": "专业内课程",
}


def _course_type_menu(item: dict[str, Any]) -> dict[str, str]:
    code = _text(item.get("code") or item.get("teachingClassType"))
    official_name = _text(item.get("name") or item.get("displayName"))
    return {
        "code": code,
        "name": official_name if official_name and official_name != code else _COURSE_TYPE_NAMES.get(code, "其他课程"),
    }
_EXAM_TYPE_NAMES = {"01": "考试", "02": "考查"}
_SCORE_SCALE_NAMES = {"100": "百分制", "200": "等级制", "300": "两级制"}
_JWXK_CAMPUS_NAMES = {"00": "南湖校区", "01": "浑南校区"}


def normalize_jwxk_campus_code(value: Any) -> str:
    text = _text(value)
    return next((code for code, name in _JWXK_CAMPUS_NAMES.items() if text == name), text)


def jwxk_campus_label(value: Any, display: Any = "") -> str:
    code = normalize_jwxk_campus_code(value)
    label = _text(display)
    if label and label != code:
        return label
    if code in _JWXK_CAMPUS_NAMES:
        return _JWXK_CAMPUS_NAMES[code]
    return "其他校区" if re.fullmatch(r"[A-Za-z0-9_-]+", code) else (label or _text(value))
_ORDER_PATTERN = re.compile(r"^[+-]?[A-Z][A-Z0-9_]{0,31}$")
_WEEK_MASK_PATTERN = re.compile(r"^[01]{1,30}$")
_CHINESE_SECTION_NUMBERS = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
    "七": 7, "八": 8, "九": 9, "十": 10, "十一": 11, "十二": 12,
}


def _payload(response: requests.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as error:
        raise JwxkError("选课系统返回了无法解析的数据") from error
    if not isinstance(payload, dict):
        raise JwxkError("选课系统响应格式已变化")
    if str(payload.get("code")) != "200":
        raise JwxkError(str(payload.get("msg") or "选课系统请求失败"))
    return payload


def _text(value: Any) -> str:
    return str(value or "").strip()


def _display_code(value: Any, display: Any, names: dict[str, str]) -> tuple[str, str]:
    code = _text(value)
    label = _text(display)
    if label and label != code:
        return code, label
    return code, names.get(code, "")


def normalize_course_category(value: Any) -> str:
    """Return a product taxonomy while preserving the official label separately."""

    text = re.sub(r"\s+", "", _text(value))
    if text in {"通识选修类", "通识选修课", "通识选修课程", "通识选修"}:
        return "通识选修"
    return re.sub(r"课程$", "", text)


def course_categories_equivalent(left: Any, right: Any) -> bool:
    """Compare official category labels while preserving their display text."""

    def key(value: Any) -> str:
        return re.sub(r"(?:课程|课|类|模块)$", "", normalize_course_category(value))

    return bool(key(left)) and key(left) == key(right)


def _teacher_details(value: Any) -> list[dict[str, str]]:
    teachers = []
    for raw in _text(value).split(","):
        raw = raw.strip()
        if not raw:
            continue
        parts = [part.strip() for part in raw.split("|")]
        label = parts[0]
        title_match = re.search(r"[（(]([^()（）]+)[）)]$", label)
        teachers.append({
            "name": re.sub(r"[（(][^()（）]+[）)]$", "", label).strip(),
            "teacher_id": parts[1] if len(parts) > 1 else "",
            "title": title_match.group(1).strip() if title_match else "",
        })
    return teachers


def _target_classes(course: dict[str, Any]) -> list[str]:
    values = []
    for source in (course.get("TJBJ"), course.get("schoolClassMapStr")):
        values.extend(part.strip() for part in _text(source).split(",") if part.strip())
    for limit_key in ("underLimitKind", "postLimitKind"):
        limit = course.get(limit_key) if isinstance(course.get(limit_key), dict) else {}
        for target in limit.get("teachingTargets") or []:
            if isinstance(target, dict) and _text(target.get("className")):
                values.append(_text(target.get("className")))
    return list(dict.fromkeys(values))


_COURSE_DETAIL_LABELS = {
    "课程代码": "course_code", "课程号": "course_code", "课程编号": "course_code",
    "课程名称": "course_name", "中文名称": "course_name", "英文名称": "english_name",
    "学分": "credits", "学时": "hours", "总学时": "hours",
    "开课单位": "department", "开课学院": "department", "学院": "department",
    "课程性质": "course_nature", "课程类别": "course_category",
    "通识选修课类别": "general_elective_category",
    "通识选修类别": "general_elective_category",
    "考试类型": "exam_type", "考核方式": "exam_type",
    "成绩分制": "score_scale", "成绩记载方式": "score_scale",
    "课程简介": "description", "课程介绍": "description",
}


def parse_course_detail_html(html: str) -> dict[str, str]:
    """Extract only student-facing fields from the official course detail page."""

    soup = BeautifulSoup(html or "", "lxml")
    result: dict[str, str] = {}

    def accept(label: Any, value: Any) -> None:
        normalized_label = re.sub(r"[\s:：]", "", _text(label))
        key = _COURSE_DETAIL_LABELS.get(normalized_label)
        clean_value = re.sub(r"\s+", " ", _text(value))
        if key and clean_value and not result.get(key):
            result[key] = clean_value

    for row in soup.select("tr"):
        cells = row.find_all(["th", "td"], recursive=False)
        if len(cells) >= 2:
            for index in range(0, len(cells) - 1, 2):
                accept(cells[index].get_text(" ", strip=True), cells[index + 1].get_text(" ", strip=True))
    for term in soup.select("dt"):
        value = term.find_next_sibling("dd")
        if value:
            accept(term.get_text(" ", strip=True), value.get_text(" ", strip=True))
    for label in soup.select("label"):
        value = label.find_next_sibling()
        if value:
            accept(label.get_text(" ", strip=True), value.get_text(" ", strip=True))
    return result


def _number(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _find_named_number(value: Any, names: set[str]) -> int | None:
    """Find an official numeric field without retaining its personal payload."""

    normalized = {name.casefold() for name in names}
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).casefold() in normalized:
                parsed = _number(item)
                if parsed is not None:
                    return parsed
        for item in value.values():
            parsed = _find_named_number(item, names)
            if parsed is not None:
                return parsed
    elif isinstance(value, list):
        for item in value:
            parsed = _find_named_number(item, names)
            if parsed is not None:
                return parsed
    elif isinstance(value, str):
        match = re.search(r"剩余权重\s*[:：]?\s*(\d+)", value)
        if match:
            return int(match.group(1))
    return None


def _weeks_from_mask(value: Any) -> tuple[int, ...]:
    mask = _text(value)
    if not _WEEK_MASK_PATTERN.fullmatch(mask):
        return ()
    return tuple(index for index, enabled in enumerate(mask, 1) if enabled == "1")


def _weekday_from_text(value: Any) -> int | None:
    match = re.search(r"(?:星期|周)\s*([一二三四五六日天])", _text(value))
    if not match:
        return None
    label = "日" if match.group(1) == "天" else match.group(1)
    return "一二三四五六日".index(label) + 1


def _section_number(value: str) -> int | None:
    text = value.strip()
    if text.isdigit():
        return int(text)
    return _CHINESE_SECTION_NUMBERS.get(text)


def _sections_from_text(value: Any) -> tuple[int | None, int | None]:
    text = _text(value)
    numeric = re.search(r"第?\s*(\d{1,2})\s*(?:[-~～—至]\s*第?\s*(\d{1,2}))?\s*节", text)
    if numeric:
        start = int(numeric.group(1))
        return start, int(numeric.group(2) or start)
    chinese = re.search(
        r"第\s*([一二三四五六七八九十]{1,3})\s*节"
        r"(?:\s*[-~～—至]\s*第\s*([一二三四五六七八九十]{1,3})\s*节)?",
        text,
    )
    if not chinese:
        return None, None
    start = _section_number(chinese.group(1))
    return start, _section_number(chinese.group(2)) if chinese.group(2) else start


def _schedule_fragments(value: Any) -> list[dict[str, Any]]:
    fragments = []
    for raw in re.split(r"[，；;\n]+", _text(value)):
        text = raw.strip().strip("，；;")
        if not text:
            continue
        parts = [part.strip() for part in text.split("/")]
        start, end = _sections_from_text(parts[2] if len(parts) > 2 else text)
        week_text = re.sub(r"\[[^\]]*\]", "", parts[0] if parts else "").strip()
        fragments.append({
            "raw_text": text,
            "week_text": week_text,
            "weeks": tuple(parse_weeks(week_text)),
            "weekday": _weekday_from_text(parts[1] if len(parts) > 1 else text),
            "start_section": start,
            "end_section": end,
            "teacher": parts[-2] if len(parts) >= 5 else "",
            "location": parts[-1] if len(parts) >= 5 else "",
        })
    return fragments


def _matching_fragment(
    fragments: list[dict[str, Any]], *, weeks: tuple[int, ...], weekday: int | None,
    start_section: int | None, end_section: int | None, teacher: str,
) -> dict[str, Any] | None:
    ranked = []
    for fragment in fragments:
        score = 0
        if weekday and fragment.get("weekday") == weekday:
            score += 8
        elif weekday and fragment.get("weekday"):
            continue
        if start_section and fragment.get("start_section") == start_section:
            score += 4
        elif start_section and fragment.get("start_section"):
            continue
        if end_section and fragment.get("end_section") == end_section:
            score += 4
        elif end_section and fragment.get("end_section"):
            continue
        fragment_teacher = _text(fragment.get("teacher"))
        if teacher and fragment_teacher and teacher == fragment_teacher:
            score += 2
        if weeks and fragment.get("weeks"):
            if weeks == fragment["weeks"]:
                score += 3
            elif set(weeks) & set(fragment["weeks"]):
                score += 1
            else:
                continue
        ranked.append((score, fragment))
    if not ranked:
        return None
    ranked.sort(key=lambda item: item[0], reverse=True)
    if len(ranked) > 1 and ranked[0][0] == ranked[1][0]:
        return None
    return ranked[0][1]


def _normalize_class(row: dict[str, Any], parent: dict[str, Any]) -> dict[str, Any]:
    parent_selected = _text(parent.get("SFYX")) == "1"
    course = {**parent, **row}
    class_id = _text(course.get("JXBID") or course.get("KXH"))
    official_schedule = _text(course.get("YPSJDD") or course.get("teachingPlace"))
    fragments = _schedule_fragments(official_schedule)
    schedules = []
    for index, item in enumerate(row.get("SKSJ") or []):
        if not isinstance(item, dict):
            continue
        week_text = _text(item.get("SKZCMC"))
        week_mask = _text(item.get("SKZC"))
        mask_weeks = _weeks_from_mask(week_mask)
        text_weeks = tuple(parse_weeks(week_text))
        if mask_weeks:
            weeks = mask_weeks
            parse_status = "mismatch" if text_weeks and text_weeks != mask_weeks else "parsed"
        elif text_weeks:
            weeks = text_weeks
            parse_status = "fallback_text"
        else:
            weeks = ()
            parse_status = "unknown"
        weekday = _number(item.get("SKXQ"))
        start_section = _number(item.get("KSJC"))
        end_section = _number(item.get("JSJC"))
        teacher = _text(item.get("SKJS"))
        fragment = _matching_fragment(
            fragments, weeks=weeks, weekday=weekday,
            start_section=start_section, end_section=end_section, teacher=teacher,
        )
        stable = "|".join((
            class_id, str(index), week_mask or week_text, str(weekday or 0),
            str(start_section or 0), str(end_section or 0), teacher,
        ))
        schedules.append({
            "meeting_id": "jwxk-mtg-" + hashlib.sha256(stable.encode("utf-8")).hexdigest()[:20],
            "term_code": _text(item.get("XNXQ")),
            "week_text": week_text,
            "week_mask": week_mask,
            "weeks": list(weeks),
            "recurrence_unknown": not bool(weeks),
            "parse_status": parse_status,
            "weekday": weekday,
            "start_section": start_section,
            "end_section": end_section,
            "teacher": teacher,
            "campus": normalize_jwxk_campus_code(item.get("XXXQDM")),
            "campus_name": jwxk_campus_label(
                item.get("XXXQDM"), item.get("XXXQDM_DISPLAY") or item.get("XXXQMC")
            ),
            "location": _text(fragment.get("location")) if fragment else "",
            "raw_text": _text(fragment.get("raw_text")) if fragment else "",
        })
    locations = list(dict.fromkeys(
        _text(item.get("location")) for item in schedules if _text(item.get("location"))
    ))
    capacity = _number(course.get("KRL"))
    if capacity is None:
        capacity = _number(course.get("classCapacity"))
    selected = _number(course.get("YXRS"))
    if selected is None:
        selected = _number(course.get("numberOfSelected"))
    exam_type_code, exam_type = _display_code(
        course.get("KSLX") or course.get("examType"),
        course.get("KSLXMC") or course.get("examTypeName"),
        _EXAM_TYPE_NAMES,
    )
    score_scale_code, score_scale = _display_code(
        course.get("CJJLFS"), course.get("CJJLFSMC"), _SCORE_SCALE_NAMES,
    )
    official_category = _text(course.get("KCLB") or course.get("courseType"))
    general_elective_category = _text(
        course.get("XGXKLB") or course.get("XGXKLBMC")
        or course.get("XGXKLBDM_DISPLAY")
        or course.get("generalElectiveCategoryName")
    )
    general_elective_category_code = _text(
        course.get("XGXKLBDM") or course.get("generalElectiveCategoryCode")
    )
    campus_code = normalize_jwxk_campus_code(
        course.get("XQDM") or course.get("campusCode")
        or next((item.get("campus") for item in schedules if item.get("campus")), "")
        or course.get("campus") or course.get("XQ") or course.get("teachCampus")
    )
    campus_name = jwxk_campus_label(campus_code,
        course.get("XQMC") or course.get("campusName") or course.get("teachCampusName")
        or course.get("XQ")
        or next((item.get("campus_name") for item in schedules if item.get("campus_name")), "")
    )
    return {
        "course_code": _text(course.get("KCH")),
        "course_name": _text(course.get("KCM")),
        "class_id": class_id,
        "class_number": _text(course.get("KXH")),
        "credits": _text(course.get("XF")),
        "hours": _text(course.get("XS") or course.get("hours")),
        "teacher": _text(course.get("SKJS")),
        "location": "；".join(locations),
        "official_schedule": official_schedule,
        "campus": campus_code,
        "campus_name": campus_name,
        "department": _text(
            course.get("KKDW") or course.get("CDDW") or course.get("departmentName")
        ),
        "course_nature": _text(course.get("KCXZ") or course.get("courseNature")),
        "course_category": official_category,
        "course_categories": [official_category] if official_category else [],
        "normalized_course_category": normalize_course_category(official_category),
        "general_elective_category_code": general_elective_category_code,
        "general_elective_category": general_elective_category,
        "exam_type_code": exam_type_code,
        "exam_type": exam_type,
        "score_scale_code": score_scale_code,
        "score_scale": score_scale,
        # teachingClassType is the official mutation clazzType carried by the
        # row.  It is not the same thing as the catalog query scope: ALLKC is
        # only the all-course search entry and must never be posted as clazzType.
        "teaching_class_type": _text(
            course.get("teachingClassType") or course.get("clazzType")
        ),
        "teaching_mode": _text(course.get("XSXLX")),
        "teacher_details": _teacher_details(course.get("SKJSLB")),
        "teacher_titles": _text(course.get("SKJSZC")),
        "target_classes": _target_classes(course),
        "capacity": capacity,
        "selected_count": selected,
        "first_choice_count": _number(course.get("DYZYRS") or course.get("numberOfFirstVolunteer")),
        "weight_participant_count": _number(course.get("QZXKRS")),
        "devoted_weight": _number(course.get("TRQZ")),
        "selection_source": _text(course.get("_selection_source")),
        "conflict": _text(course.get("SFCT")) == "1",
        "conflict_description": _text(course.get("conflictDesc")),
        "restricted": _text(course.get("SFXZXK")) == "1",
        "eligibility_status": "unknown",
        "eligibility_reason": "",
        "full": _text(course.get("SFYM")) == "1" or (
            capacity is not None and selected is not None and capacity > 0 and selected >= capacity
        ),
        "selected": _text(course.get("SFYX")) == "1",
        "course_already_selected": parent_selected or _text(course.get("SFYX")) == "1",
        "has_test": _text(course.get("hasTest")) == "1",
        "has_book": _text(course.get("hasBook")) == "1",
        "notice": _text(course.get("XKSM")),
        "schedules": schedules,
    }


def normalize_course_rows(rows: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for parent in rows if isinstance(rows, list) else []:
        if not isinstance(parent, dict):
            continue
        children = parent.get("tcList")
        if isinstance(children, list) and children:
            result.extend(
                _normalize_class(child, parent)
                for child in children if isinstance(child, dict)
            )
        else:
            result.append(_normalize_class(parent, {}))
    return result


def apply_selection_market_semantics(
    courses: list[dict[str, Any]], selection_type_code: str,
) -> list[dict[str, Any]]:
    """Attach the correct anonymous participant metric for the active round."""

    is_weight_round = _text(selection_type_code) == "04"
    for course in courses:
        participant_count = (
            course.get("weight_participant_count")
            if is_weight_round else course.get("selected_count")
        )
        course["selection_type_code"] = _text(selection_type_code)
        course["market_participant_count"] = participant_count
        course["market_participant_label"] = "已投注人数" if is_weight_round else "已选人数"
        # 权重轮次允许投注人数超过容量，不能用 YXRS 或容量边界阻止投权。
        if is_weight_round:
            course["full"] = False
    return courses


def parse_course_eligibility(payload: Any, *, class_id: str = "") -> dict[str, Any]:
    """Extract official selectability without exposing student identity fields."""
    body = payload if isinstance(payload, dict) else {}
    raw_lines = body.get("data") if isinstance(body.get("data"), list) else []
    lines = [_text(line) for line in raw_lines if _text(line)]
    status = "unknown"
    for line in lines:
        compact = re.sub(r"\s+", "", line)
        if "是否可选" not in compact:
            continue
        if "不可选" in compact:
            status = "unavailable"
        elif "可选" in compact:
            status = "selectable"
        break
    ignored_prefixes = ("是否可选", "学生", "教学班", "选课轮次")
    reasons = [line for line in lines if not line.startswith(ignored_prefixes)]
    message = _text(body.get("msg"))
    if status == "unavailable":
        reason = "；".join(reasons) or message or "当前轮次不可选择该教学班"
    elif status == "selectable":
        reason = ""
    else:
        reason = "；".join(reasons) or message or "官方暂未返回明确的可选结果"
    return {"class_id": class_id, "status": status, "reason": reason}


def normalize_saved_plan_items(rows: Any) -> list[dict[str, Any]]:
    """Migrate plans saved before structured JWXK meeting fields existed."""
    result = []
    for raw in rows if isinstance(rows, list) else []:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        official_schedule = _text(item.get("official_schedule"))
        legacy_location = _text(item.get("location"))
        if not official_schedule and ("/星期" in legacy_location or "/周" in legacy_location):
            official_schedule = legacy_location
        fragments = _schedule_fragments(official_schedule)
        schedules = []
        for index, raw_meeting in enumerate(item.get("schedules") or []):
            if not isinstance(raw_meeting, dict):
                continue
            meeting = dict(raw_meeting)
            mask_weeks = _weeks_from_mask(meeting.get("week_mask"))
            text_weeks = tuple(parse_weeks(meeting.get("week_text")))
            weeks = tuple(meeting.get("weeks") or ()) or mask_weeks or text_weeks
            meeting["weeks"] = list(weeks)
            meeting["recurrence_unknown"] = not bool(weeks)
            inferred_parse_status = (
                "mismatch" if mask_weeks and text_weeks and mask_weeks != text_weeks
                else "parsed" if mask_weeks
                else "fallback_text" if text_weeks
                else "unknown"
            )
            if not _text(meeting.get("parse_status")):
                meeting["parse_status"] = inferred_parse_status
            fragment = _matching_fragment(
                fragments,
                weeks=weeks,
                weekday=_number(meeting.get("weekday")),
                start_section=_number(meeting.get("start_section")),
                end_section=_number(meeting.get("end_section")),
                teacher=_text(meeting.get("teacher") or item.get("teacher")),
            )
            meeting_location = _text(meeting.get("location"))
            if not meeting_location or "/星期" in meeting_location or "/周" in meeting_location:
                meeting["location"] = _text(fragment.get("location")) if fragment else ""
            stable = "|".join((
                _text(item.get("class_id")), str(index), _text(meeting.get("week_mask") or meeting.get("week_text")),
                _text(meeting.get("weekday")), _text(meeting.get("start_section")),
                _text(meeting.get("end_section")), _text(meeting.get("teacher") or item.get("teacher")),
            ))
            meeting.setdefault(
                "meeting_id",
                "jwxk-mtg-" + hashlib.sha256(stable.encode("utf-8")).hexdigest()[:20],
            )
            schedules.append(meeting)
        item["schedules"] = schedules
        item["official_schedule"] = official_schedule
        # Early workspace builds grouped legacy candidates in the UI by course
        # code/class id without persisting that group id on the item.  Such an
        # item could visually recreate its group immediately after the user
        # deleted it because the delete operation only matched plan_group_id.
        # Persist the same stable fallback during normalization so old plans
        # become fully editable and remain isolated inside their saved batch.
        plan_group_id = _text(
            item.get("plan_group_id") or item.get("group_id")
            or item.get("course_code") or item.get("class_id")
        )
        if plan_group_id:
            item["plan_group_id"] = plan_group_id
            item.setdefault(
                "plan_group_name",
                _text(item.get("course_name")) or "方案组",
            )
            item.setdefault("plan_group_target_count", 1)
        locations = list(dict.fromkeys(
            _text(meeting.get("location")) for meeting in schedules if _text(meeting.get("location"))
        ))
        item["location"] = "；".join(locations)
        result.append(item)
    return result


def _course_group_id(course: dict[str, Any]) -> str:
    identity = "|".join((
        _text(course.get("course_code")).casefold(),
        _text(course.get("course_name")).casefold(),
        _text(course.get("credits")),
        _text(course.get("department")).casefold(),
    ))
    return "jwxk-course-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]


def group_course_rows(
    rows: list[dict[str, Any]], *, source_tags: dict[str, set[str]] | None = None
) -> list[dict[str, Any]]:
    """Collapse official teaching-class rows into student-facing courses."""
    grouped: dict[str, dict[str, Any]] = {}
    source_tags = source_tags or {}
    for original_row in rows:
        row = dict(original_row)
        exam_code = _text(row.get("exam_type_code") or row.get("exam_type"))
        if exam_code in _EXAM_TYPE_NAMES:
            row["exam_type_code"] = exam_code
            row["exam_type"] = _EXAM_TYPE_NAMES[exam_code]
        scale_code = _text(row.get("score_scale_code"))
        if not scale_code and _text(row.get("score_scale")) in _SCORE_SCALE_NAMES:
            scale_code = _text(row.get("score_scale"))
        if scale_code in _SCORE_SCALE_NAMES:
            row["score_scale_code"] = scale_code
            row["score_scale"] = _SCORE_SCALE_NAMES[scale_code]
        row.setdefault("course_categories", [row.get("course_category")] if row.get("course_category") else [])
        row.setdefault("normalized_course_category", normalize_course_category(row.get("course_category")))
        group_id = _course_group_id(row)
        group = grouped.setdefault(group_id, {
            "group_id": group_id,
            "course_code": _text(row.get("course_code")),
            "course_name": _text(row.get("course_name")) or "未命名课程",
            "credits": _text(row.get("credits")),
            "hours": _text(row.get("hours")),
            "department": _text(row.get("department")),
            "course_nature": _text(row.get("course_nature")),
            "course_category": _text(row.get("course_category")),
            "course_categories": [],
            "normalized_course_category": _text(row.get("normalized_course_category")),
            "general_elective_category_code": _text(row.get("general_elective_category_code")),
            "general_elective_category": _text(row.get("general_elective_category")),
            "exam_type_code": _text(row.get("exam_type_code")),
            "exam_type": _text(row.get("exam_type")),
            "score_scale_code": _text(row.get("score_scale_code")),
            "score_scale": _text(row.get("score_scale")),
            "source_tags": sorted(source_tags.get(_text(row.get("course_code")), set())),
            "campuses": [],
            "classes": [],
        })
        for key in (
            "credits", "hours", "department", "course_nature", "course_category",
            "normalized_course_category", "general_elective_category_code",
            "general_elective_category", "exam_type_code", "exam_type",
            "score_scale_code", "score_scale",
        ):
            if not _text(group.get(key)) and _text(row.get(key)):
                group[key] = _text(row.get(key))
        group["course_categories"] = list(dict.fromkeys([
            *(group.get("course_categories") or []),
            *(row.get("course_categories") or []),
            *([_text(row.get("course_category"))] if _text(row.get("course_category")) else []),
        ]))
        if any(normalize_course_category(value) == "通识选修" for value in group["course_categories"]):
            group["normalized_course_category"] = "通识选修"
        campus_labels = [
            jwxk_campus_label(row.get("campus"), row.get("campus_name")),
            *[
                jwxk_campus_label(item.get("campus"), item.get("campus_name"))
                for item in row.get("schedules") or []
            ],
        ]
        group["campuses"] = list(dict.fromkeys([
            *(group.get("campuses") or []),
            *(value for value in campus_labels if value),
        ]))
        group["classes"].append(row)
    for group in grouped.values():
        classes = group["classes"]
        classes.sort(key=lambda item: (
            bool(item.get("restricted") or item.get("full")),
            bool(item.get("conflict")),
            -max(0, (item.get("capacity") or 0) - (
                item.get("market_participant_count")
                if item.get("market_participant_count") is not None
                else item.get("selected_count") or 0
            )),
            _text(item.get("teacher")),
        ))
        group["class_count"] = len(classes)
        group["selectable_count"] = sum(
            item.get("eligibility_status") == "selectable"
            and not item.get("restricted") and not item.get("full")
            for item in classes
        )
        group["eligibility_pending_count"] = sum(
            item.get("eligibility_status") == "unknown" for item in classes
        )
        group["available_count"] = sum(
            item.get("capacity") is not None
            and (
                item.get("market_participant_count")
                if item.get("market_participant_count") is not None
                else item.get("selected_count")
            ) is not None
            and item["capacity"] > (
                item.get("market_participant_count")
                if item.get("market_participant_count") is not None
                else item["selected_count"]
            )
            for item in classes
        )
        group["conflict_free_count"] = sum(not item.get("conflict") for item in classes)
    return list(grouped.values())


class JwxkSessionClient:
    """Authenticated JWXK adapter on the shared CAS session."""

    def __init__(self, auth: "NEUAuthClient", *, network_mode: str = "direct"):
        self.auth = auth
        self.network_mode = network_mode

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        return self.auth.request_service(
            "jwxk", method, path,
            network_mode_override=self.network_mode,
            **kwargs,
        )

    def _post_form(self, path: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        return _payload(self._request("POST", path, data=data or {}))

    def _check_one_course_eligibility(
        self, *, batch_code: str, class_id: str, secret_val: str = ""
    ) -> dict[str, Any]:
        response = self._request(
            "POST",
            "/xsxk/elective/check",
            data={"clazzId": class_id, "secretVal": secret_val},
            headers={"batchid": batch_code},
        )
        try:
            payload = response.json()
        except ValueError as error:
            raise JwxkError("选课系统没有返回有效的可选性检查结果") from error
        return parse_course_eligibility(payload, class_id=class_id)

    def check_course_eligibility(
        self, *, batch_code: str, class_ids: list[str]
    ) -> dict[str, Any]:
        self._activate_batch(batch_code)
        unique_ids = list(dict.fromkeys(_text(value) for value in class_ids if _text(value)))
        return {
            "_account": str(getattr(self.auth, "username", "") or ""),
            "results": [
                self._check_one_course_eligibility(
                    batch_code=batch_code,
                    class_id=class_id,
                )
                for class_id in unique_ids
            ]
        }

    def get_context(self) -> dict[str, Any]:
        self.auth.ensure_service_session("jwxk", network_mode_override=self.network_mode)
        now = self._post_form("/xsxk/web/now").get("data") or {}
        token = self.auth.get_service_token("jwxk")
        student_payload = self._post_form(
            "/xsxk/web/studentInfo", {"token": token or ""}
        )
        student = (student_payload.get("data") or {}).get("student") or {}
        rows = list(student.get("electiveBatchList") or []) + list(
            student.get("expElectiveBatchList") or []
        )
        batches = parse_account_batches(rows, official_now=now.get("currentTime"))
        try:
            public_by_code = {
                item.code: item for item in JwxkPublicClient(timeout=self.auth.timeout).get_batches()
            }
        except (JwxkError, requests.RequestException):
            public_by_code = {}
        merged_batches = []
        for batch in batches:
            public = public_by_code.get(batch.code)
            course_types = batch.course_types or (public.course_types if public else ())
            menus = batch.menus or tuple(
                {"code": code, "name": _COURSE_TYPE_NAMES.get(code, "其他课程")}
                for code in course_types
            )
            merged_batches.append(replace(batch, course_types=course_types, menus=menus))
        return {
            "official_time": _text(now.get("currentTime")),
            "online_count": _number(now.get("onlineCount")),
            "current_campus": normalize_jwxk_campus_code(student.get("campus")),
            "current_campus_name": jwxk_campus_label(student.get("campus"), student.get("campusName")),
            "batches": merged_batches,
        }

    def _activate_batch(self, batch_code: str) -> dict[str, Any]:
        payload = self._post_form("/xsxk/elective/user", {"batchId": batch_code})
        return (payload.get("data") or {}).get("student") or {}

    def get_weight_budget(self, *, batch_code: str) -> dict[str, Any]:
        """Read the active round's official remaining weight."""

        payload = self._post_form("/xsxk/elective/user", {"batchId": batch_code})
        context = payload.get("data") or {}
        student = context.get("student") if isinstance(context, dict) else None
        student = student if isinstance(student, dict) else {}
        batch_rows = [
            *(student.get("electiveBatchList") or []),
            *(student.get("expElectiveBatchList") or []),
        ]
        batch_row = next((
            row for row in batch_rows
            if _text(row.get("code") or row.get("batchCode") or row.get("id")) == batch_code
        ), {})
        term_code = _text(
            batch_row.get("termCode") or batch_row.get("schoolTerm")
            or batch_row.get("semester") or batch_row.get("xnxq")
        )
        term_weight = next((
            row for row in student.get("termWeightList") or []
            if not term_code or _text(row.get("termCode")) == term_code
        ), None)
        remaining = _number((term_weight or {}).get("weight"))
        if remaining is None:
            remaining = _find_named_number(context, {
            "remainingWeight", "remainWeight", "availableWeight", "weightBalance",
            "SYQZ", "KYSYQZ", "QZYE", "surplusWeight",
            })
        total = _find_named_number(context, {
            "totalWeight", "weightTotal", "QZZE", "ZQZ",
        })
        minimum = _find_named_number(context, {
            "minimumWeight", "minWeight", "minimumBid", "minBid", "ZXQZ",
        }) or 5
        step = _find_named_number(context, {
            "weightStep", "bidStep", "QZBC",
        }) or 1
        selected = self.get_selected(batch_code=batch_code)
        devoted = sum(
            int(row.get("devoted_weight") or 0)
            for row in selected.get("volunteered") or []
        )
        if remaining is None and total is not None:
            remaining = max(0, total - devoted)
        if remaining is None:
            raise JwxkError("官方选课系统暂未返回剩余权重，请刷新轮次后重试")
        if total is None:
            total = remaining + devoted
        return {
            "remaining": remaining,
            "total": total,
            "used": max(0, total - remaining),
            "minimum": minimum,
            "step": step,
            "source": "official_round_context",
        }

    def _batch_for_mutation(self, batch_code: str) -> JwxkBatch:
        context = self.get_context()
        batch = next((item for item in context["batches"] if item.code == batch_code), None)
        if batch is None:
            raise JwxkError("选课轮次不存在或当前账号不可见")
        if not batch.account_selectable:
            raise JwxkError("当前账号不能进入该选课轮次")
        if batch.state != "active":
            raise JwxkError("当前不在该轮次的选课时间内")
        if batch.need_confirm and not batch.confirmed:
            raise JwxkError("请先阅读并确认该轮次须知")
        return batch

    def _search_raw(
        self, *, batch_code: str, teaching_class_type: str, keyword: str
    ) -> list[dict[str, Any]]:
        student = self._activate_batch(batch_code)
        body = {
            "teachingClassType": teaching_class_type,
            "pageNumber": 1,
            "pageSize": 50,
            "orderBy": "",
            "KEY": keyword,
        }
        if teaching_class_type != "ALLKC" and _text(student.get("campus")):
            body["campus"] = _text(student.get("campus"))
        payload = _payload(self._request(
            "POST", "/xsxk/elective/clazz/list", json=body
        ))
        return (payload.get("data") or {}).get("rows") or []

    @staticmethod
    def _raw_classes(rows: list[dict[str, Any]]):
        for parent in rows:
            children = parent.get("tcList")
            if isinstance(children, list) and children:
                for child in children:
                    if isinstance(child, dict):
                        yield {**parent, **child}
            else:
                yield parent

    def _find_raw_class(
        self,
        *,
        batch_code: str,
        teaching_class_type: str,
        class_id: str,
        course_code: str,
    ) -> dict[str, Any]:
        rows = self._search_raw(
            batch_code=batch_code,
            teaching_class_type=teaching_class_type,
            keyword=course_code or class_id,
        )
        item = next((
            row for row in self._raw_classes(rows)
            if _text(row.get("JXBID") or row.get("KXH")) == class_id
            and (not course_code or _text(row.get("KCH")) == course_code)
        ), None)
        if item is None or not _text(item.get("secretVal")):
            raise JwxkError("教学班信息已变化，请刷新课程列表后重试")
        return item

    def _resolve_mutation_class(
        self,
        *,
        batch: JwxkBatch,
        batch_code: str,
        teaching_class_type: str,
        class_id: str,
        course_code: str,
    ) -> tuple[dict[str, Any], str]:
        """Resolve a real clazzType without ever submitting the ALLKC query scope."""

        query_scopes = (
            [teaching_class_type]
            if teaching_class_type not in {"", "ALL", "ROUND"}
            else []
        )
        query_scopes.extend(
            _text(item.get("code")) for item in batch.menus
            if _text(item.get("code")) not in {"", "ALLKC"}
        )
        seen: set[str] = set()
        for query_scope in query_scopes:
            if not query_scope or query_scope in seen:
                continue
            seen.add(query_scope)
            try:
                item = self._find_raw_class(
                    batch_code=batch_code,
                    teaching_class_type=query_scope,
                    class_id=class_id,
                    course_code=course_code,
                )
            except JwxkError:
                continue
            official_type = _text(item.get("teachingClassType") or item.get("clazzType"))
            mutation_type = official_type if official_type != "ALLKC" else ""
            if not mutation_type and query_scope != "ALLKC":
                mutation_type = query_scope
            if mutation_type:
                return item, mutation_type
        raise JwxkError("只能在全校课程查询中找到该教学班，暂时无法确认其真实投选类型，请刷新目录后重试")

    def get_catalog_detail(
        self, *, batch_code: str, teaching_class_type: str,
        course_code: str, class_id: str,
    ) -> dict[str, Any]:
        """Read fresh course and teaching-class details without exposing write tokens."""

        raw = self._find_raw_class(
            batch_code=batch_code,
            teaching_class_type=teaching_class_type,
            class_id=class_id,
            course_code=course_code,
        )
        normalized = _normalize_class(raw, {})
        course_detail: dict[str, str] = {}
        try:
            response = self._request("GET", f"/xsxk/web/kc/{quote(course_code, safe='')}")
            if response.ok and "html" in _text(response.headers.get("content-type")).casefold():
                course_detail = parse_course_detail_html(response.text)
        except (JwxkError, requests.RequestException):
            # The class payload already contains all selection-critical fields.
            # A temporarily unavailable descriptive page must not hide them.
            course_detail = {}

        for key in (
            "course_name", "credits", "hours", "department", "course_nature",
            "course_category", "general_elective_category", "exam_type", "score_scale",
        ):
            if _text(course_detail.get(key)):
                normalized[key] = _text(course_detail[key])
        if _text(course_detail.get("course_category")):
            official = _text(course_detail["course_category"])
            normalized["course_categories"] = list(dict.fromkeys([
                *(normalized.get("course_categories") or []), official,
            ]))
            normalized["normalized_course_category"] = normalize_course_category(official)
        return {
            "course": {
                "course_code": normalized.get("course_code", ""),
                "course_name": normalized.get("course_name", ""),
                "english_name": course_detail.get("english_name", ""),
                "credits": normalized.get("credits", ""),
                "hours": normalized.get("hours", ""),
                "department": normalized.get("department", ""),
                "course_nature": normalized.get("course_nature", ""),
                "course_category": normalized.get("course_category", ""),
                "course_categories": normalized.get("course_categories", []),
                "normalized_course_category": normalized.get("normalized_course_category", ""),
                "general_elective_category_code": normalized.get("general_elective_category_code", ""),
                "general_elective_category": normalized.get("general_elective_category", ""),
                "exam_type_code": normalized.get("exam_type_code", ""),
                "exam_type": normalized.get("exam_type", ""),
                "score_scale_code": normalized.get("score_scale_code", ""),
                "score_scale": normalized.get("score_scale", ""),
                "description": course_detail.get("description", ""),
            },
            "teaching_class": normalized,
        }

    def _post_mutation(self, path: str, data: dict[str, Any], *, confirm_risk: bool) -> dict[str, Any]:
        # Read requests may recover CAS and retry. A mutation may already have
        # reached JWXK before an auth-shaped response is observed, so it must
        # never be replayed automatically.
        response = self._request("POST", path, data=data, retry_on_auth=False)
        try:
            payload = response.json()
        except ValueError as error:
            raise JwxkError("选课系统没有返回有效的提交结果") from error
        code = str(payload.get("code"))
        if code == "301" and confirm_risk:
            confirmed = {**data, "isConfirm": "1"}
            response = self._request("POST", path, data=confirmed, retry_on_auth=False)
            try:
                payload = response.json()
            except ValueError as error:
                raise JwxkError("选课系统没有返回有效的确认结果") from error
            code = str(payload.get("code"))
        return {
            "success": code == "200",
            "queued": code == "200",
            "requires_confirmation": code == "301",
            "code": code,
            "message": _text(payload.get("msg")) or ("已进入官方处理队列" if code == "200" else "操作失败"),
        }

    def confirm_batch(self, *, batch_code: str) -> dict[str, Any]:
        context = self.get_context()
        batch = next((item for item in context["batches"] if item.code == batch_code), None)
        if batch is None or not batch.account_selectable:
            raise JwxkError("当前账号不能确认该选课轮次")
        if not batch.need_confirm or batch.confirmed:
            return {"success": True, "queued": False, "requires_confirmation": False, "code": "200", "message": "轮次须知已确认"}
        return self._post_mutation(
            "/xsxk/elective/batch/confirm",
            {"batchId": batch_code},
            confirm_risk=False,
        )

    def select_course(
        self,
        *,
        batch_code: str,
        teaching_class_type: str,
        class_id: str,
        course_code: str,
        weight: int | None,
        confirm_risk: bool,
    ) -> dict[str, Any]:
        batch = self._batch_for_mutation(batch_code)
        official = self.get_selected(batch_code=batch_code)
        duplicate = next((
            row for row in [*(official.get("selected") or []), *(official.get("volunteered") or [])]
            if course_code and _text(row.get("course_code")).casefold() == _text(course_code).casefold()
        ), None)
        if duplicate is not None:
            raise JwxkError(
                f"已选课程中已存在同课程代码“{_text(duplicate.get('course_name')) or course_code}”，不能重复选择"
            )
        item, mutation_class_type = self._resolve_mutation_class(
            batch=batch,
            batch_code=batch_code,
            teaching_class_type=teaching_class_type,
            class_id=class_id,
            course_code=course_code,
        )
        if _text(item.get("hasTest")) == "1":
            raise JwxkError("该课程必须同时选择实验班，请刷新后在实验班选择器中完成")
        eligibility = self._check_one_course_eligibility(
            batch_code=batch_code,
            class_id=class_id,
            secret_val=_text(item.get("secretVal")),
        )
        if eligibility["status"] == "unavailable":
            raise JwxkError(eligibility["reason"] or "当前轮次不可选择该教学班")
        if eligibility["status"] != "selectable":
            raise JwxkError("暂时无法确认该教学班是否可选，请稍后重新核验")
        data = {
            "clazzType": mutation_class_type,
            "clazzId": class_id,
            "secretVal": _text(item.get("secretVal")),
            "batchId": batch_code,
            "needBook": "",
        }
        if batch.selection_type_code == "04":
            if weight is None or weight < 5:
                raise JwxkError("权重选课至少需要投入 5 点权重")
            data["weight"] = str(weight)
            path = "/xsxk/elective/neu/clazz/weightAdd"
        else:
            path = "/xsxk/elective/clazz/add"
        result = self._post_mutation(path, data, confirm_risk=confirm_risk)
        if (
            not result.get("success")
            and "已在选课结果中" in _text(result.get("message"))
        ):
            # 查询与提交之间可能被另一个标签页或官方队列选中。此时目标状态
            # 已经达成，不能让自动任务把官方 500 当成未知失败反复提交。
            result.update({
                "success": True,
                "queued": False,
                "requires_confirmation": False,
                "code": "already_selected",
                "message": "该课程已经在官方选课结果中",
            })
        result["_term_code"] = batch.term_code
        return result

    def deselect_course(
        self,
        *,
        batch_code: str,
        class_id: str,
        confirm_risk: bool,
    ) -> dict[str, Any]:
        batch = self._batch_for_mutation(batch_code)
        self._activate_batch(batch_code)
        item = None
        source = ""
        for path, source_name in (
            ("/xsxk/elective/select", "yxkcyx"),
            ("/xsxk/volunteer/select", "fakcyx"),
            ("/xsxk/volunteer/xgxk/select", "xgxkyx"),
        ):
            try:
                rows = self._post_form(path).get("data") or []
            except JwxkError:
                # The general-elective volunteer feed is absent in some rounds.
                # The first two feeds are part of the stable selected-result
                # contract: if either cannot be read, the source is ambiguous and
                # a destructive request must not be submitted with a guessed source.
                if source_name == "xgxkyx":
                    continue
                raise
            item = next((
                row for row in self._raw_classes(rows)
                if _text(row.get("JXBID") or row.get("KXH")) == class_id
            ), None)
            if item is not None:
                source = source_name
                break
        if item is None or not _text(item.get("secretVal")):
            raise JwxkError("已选课程信息已变化，请刷新后重试")
        data = {
            "clazzType": _text(item.get("teachingClassType")) or "ALLKC",
            "clazzId": class_id,
            "secretVal": _text(item.get("secretVal")),
        }
        if source:
            data["source"] = source
        if _text(item.get("electiveBatchCode")) and _text(item.get("electiveBatchCode")) != batch_code:
            data["crossBatch"] = "1"
        result = self._post_mutation(
            "/xsxk/elective/neu/clazz/del", data, confirm_risk=confirm_risk
        )
        result["_term_code"] = batch.term_code
        return result

    def search_courses(
        self,
        *,
        batch_code: str,
        teaching_class_type: str,
        page_number: int,
        page_size: int,
        keyword: str = "",
        campus: str = "",
        order_by: str = "",
        filters: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        student = self._activate_batch(batch_code)
        batch_row = next((
            row for row in [
                *(student.get("electiveBatchList") or []),
                *(student.get("expElectiveBatchList") or []),
            ]
            if _text(row.get("code") or row.get("batchCode") or row.get("id")) == batch_code
        ), {})
        result = self._search_courses_page(
            teaching_class_type=teaching_class_type, page_number=page_number,
            page_size=page_size, keyword=keyword, campus=campus,
            order_by=order_by, filters=filters,
        )
        apply_selection_market_semantics(
            result["courses"], _text(batch_row.get("typeCode") or batch_row.get("selectionTypeCode"))
        )
        return result

    def _search_courses_page(
        self, *, teaching_class_type: str, page_number: int, page_size: int,
        keyword: str = "", campus: str = "", order_by: str = "",
        filters: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        if order_by and not _ORDER_PATTERN.fullmatch(order_by):
            raise ValueError("invalid JWXK order field")
        campus = normalize_jwxk_campus_code(campus)
        body: dict[str, Any] = {
            "teachingClassType": teaching_class_type,
            "pageNumber": page_number,
            "pageSize": page_size,
            "orderBy": order_by,
        }
        if teaching_class_type != "ALLKC" and campus:
            body["campus"] = campus
        if keyword:
            body["KEY"] = keyword
        for key, value in (filters or {}).items():
            if key in _FILTER_FIELDS and value:
                body[key] = value
        payload = _payload(self._request(
            "POST", "/xsxk/elective/clazz/list", json=body
        ))
        data = payload.get("data") or {}
        return {
            "total": _number(data.get("total")) or 0,
            "courses": normalize_course_rows(data.get("rows")),
        }

    def get_selected(self, *, batch_code: str) -> dict[str, Any]:
        self._activate_batch(batch_code)
        feeds = (
            ("selected", "/xsxk/elective/select"),
            ("volunteered", "/xsxk/volunteer/select"),
            ("general_volunteered", "/xsxk/volunteer/xgxk/select"),
            ("withdrawal", "/xsxk/elective/neu/deselect"),
        )
        rows_by_feed: dict[str, list[dict[str, Any]]] = {}
        failures: list[tuple[str, Exception]] = []
        for name, path in feeds:
            try:
                rows_by_feed[name] = self._post_form(path).get("data") or []
            except (NEULoginError, JwxkError) as error:
                # JWXK exposes several result feeds for different round/menu
                # types.  A non-applicable feed can answer with the same
                # business 401 used for an expired token.  Once another feed
                # succeeds, treat that endpoint as unavailable instead of
                # discarding the complete selected-result response.
                failures.append((name, error))
                rows_by_feed[name] = []
        if failures and len(failures) == len(feeds):
            raise failures[0][1]
        for name, error in failures:
            logger.info(
                "jwxk selected feed unavailable feed=%s error=%s",
                name,
                type(error).__name__,
            )
        selected = rows_by_feed["selected"]
        volunteered = rows_by_feed["volunteered"]
        general_volunteered = rows_by_feed["general_volunteered"]
        withdrawal = rows_by_feed["withdrawal"]
        def tagged(rows, source):
            return [
                {**row, "_selection_source": source}
                for row in rows if isinstance(row, dict)
            ]
        return {
            "selected": normalize_course_rows(tagged(selected, "yxkcyx")),
            "volunteered": [
                *normalize_course_rows(tagged(volunteered, "fakcyx")),
                *normalize_course_rows(tagged(general_volunteered, "xgxkyx")),
            ],
            "withdrawal": normalize_course_rows(withdrawal),
        }

    def search_catalog(
        self, *, batch_code: str, page_number: int, page_size: int,
        keyword: str = "", scope: str = "ROUND", campus: str = "",
        order_by: str = "", filters: dict[str, str] | None = None,
        time_slot: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        context = self.get_context()
        batch = next((item for item in context["batches"] if item.code == batch_code), None)
        if batch is None:
            raise JwxkError("选课轮次不存在或当前账号不可见")
        menu_codes = [str(item.get("code") or "") for item in batch.menus if str(item.get("code") or "")]
        round_scopes = [code for code in menu_codes if code != "ALLKC"] or (["ALLKC"] if "ALLKC" in menu_codes else [])
        all_scopes = list(dict.fromkeys(menu_codes or round_scopes))
        effective_scope = scope if scope in menu_codes or scope in {"ALL", "ROUND"} else "ALL"
        campus = normalize_jwxk_campus_code(campus)
        self._activate_batch(batch_code)
        remote_filters = dict(filters or {})
        requested_category = _text(remote_filters.get("KCLB"))
        if requested_category:
            # Category display labels vary between scopes.  Sending labels such
            # as 专业方向类/专业方向课 directly makes the official endpoint
            # silently return an empty page, so compare their semantics locally.
            remote_filters.pop("KCLB", None)
        if time_slot:
            remote_filters["SKXQ"] = str(time_slot["weekday"])

        def category_matches(course: dict[str, Any]) -> bool:
            if not requested_category:
                return True
            return any(course_categories_equivalent(value, requested_category) for value in [
                course.get("course_category"),
                *(course.get("course_categories") or []),
            ])

        def meeting_matches(course: dict[str, Any]) -> bool:
            if not time_slot:
                return True
            return any(
                meeting.get("weekday") == time_slot["weekday"]
                and (meeting.get("start_section") or 0) <= time_slot["section"]
                <= (meeting.get("end_section") or 0)
                for meeting in course.get("schedules") or []
            )

        def campus_matches(course: dict[str, Any]) -> bool:
            if not campus:
                return True
            values = {
                normalize_jwxk_campus_code(course.get("campus")),
                normalize_jwxk_campus_code(course.get("campus_name")),
                *(normalize_jwxk_campus_code(item.get("campus")) for item in course.get("schedules") or []),
                *(normalize_jwxk_campus_code(item.get("campus_name")) for item in course.get("schedules") or []),
            }
            return campus in values

        def scan_scope(teaching_class_type: str) -> tuple[int, list[dict[str, Any]]]:
            local_campus_filter = bool(campus and teaching_class_type == "ALLKC")
            requires_full_scan = bool(
                requested_category or time_slot or local_campus_filter
                or (campus and effective_scope in {"ALL", "ROUND"})
            )
            if not requires_full_scan:
                result = self._search_courses_page(
                    teaching_class_type=teaching_class_type, page_number=page_number,
                    page_size=page_size, keyword=keyword, campus=campus,
                    order_by=order_by, filters=remote_filters,
                )
                return result["total"], result["courses"]
            courses: list[dict[str, Any]] = []
            remote_page = 1
            remote_total = 0
            while remote_page <= 60:
                result = self._search_courses_page(
                    teaching_class_type=teaching_class_type, page_number=remote_page,
                    page_size=50, keyword=keyword, campus=campus,
                    order_by=order_by, filters=remote_filters,
                )
                remote_total = result["total"]
                rows = result["courses"]
                courses.extend(
                    course for course in rows
                    if category_matches(course) and meeting_matches(course) and campus_matches(course)
                )
                if not rows or remote_page * 50 >= remote_total:
                    break
                remote_page += 1
            return remote_total, courses

        if effective_scope in {"ALL", "ROUND"}:
            merged_courses: list[dict[str, Any]] = []
            round_tags_by_code: dict[str, set[str]] = {}
            merged_total = 0
            aggregate_scopes = all_scopes if effective_scope == "ALL" else round_scopes
            for round_scope in aggregate_scopes:
                scope_total, scope_courses = scan_scope(round_scope)
                merged_total += scope_total
                for course in scope_courses:
                    course["source_scopes"] = [round_scope]
                    if not _text(course.get("teaching_class_type")) and round_scope != "ALLKC":
                        course["teaching_class_type"] = round_scope
                    merged_courses.append(course)
                    code = _text(course.get("course_code"))
                    if code:
                        round_tags_by_code.setdefault(code, set()).add(
                            _COURSE_TYPE_NAMES.get(round_scope, "其他课程")
                        )
            # 同一教学班可能同时出现在培养计划、推荐和全校目录中。保留
            # 全部来源标签，但提交时优先使用具体轮次类型而不是 ALLKC。
            by_class: dict[str, dict[str, Any]] = {}
            for course in merged_courses:
                class_id = _text(course.get("class_id")) or "|".join((
                    _text(course.get("course_code")), _text(course.get("teacher")),
                    _text(course.get("class_number")),
                ))
                previous = by_class.get(class_id)
                if previous is None:
                    by_class[class_id] = course
                    continue
                source_scopes = list(dict.fromkeys([
                    *(previous.get("source_scopes") or []),
                    *(course.get("source_scopes") or []),
                ]))
                mutation_types = [
                    _text(previous.get("teaching_class_type")),
                    _text(course.get("teaching_class_type")),
                ]
                preferred_scope = next((value for value in mutation_types if value and value != "ALLKC"), None)
                merged = {**previous}
                for key, value in course.items():
                    if key not in merged or value not in (None, "", [], {}):
                        merged[key] = value
                merged["course_categories"] = list(dict.fromkeys([
                    *(previous.get("course_categories") or []),
                    *(course.get("course_categories") or []),
                ]))
                if any(normalize_course_category(value) == "通识选修" for value in merged["course_categories"]):
                    merged["normalized_course_category"] = "通识选修"
                by_class[class_id] = {
                    **merged,
                    "source_scopes": [value for value in source_scopes if value],
                    "teaching_class_type": preferred_scope,
                }
            merged_courses = list(by_class.values())
            apply_selection_market_semantics(merged_courses, batch.selection_type_code)
            all_groups = group_course_rows(merged_courses, source_tags=round_tags_by_code)
            filtered_locally = bool(requested_category or time_slot or campus)
            start = (page_number - 1) * page_size
            primary = {
                "total": len(all_groups) if filtered_locally else merged_total,
                "courses": merged_courses,
                "groups": all_groups[start:start + page_size] if filtered_locally else all_groups,
            }
        elif requested_category or time_slot or (campus and effective_scope == "ALLKC"):
            _, matched_courses = scan_scope(effective_scope)
            apply_selection_market_semantics(matched_courses, batch.selection_type_code)
            all_groups = group_course_rows(matched_courses)
            start = (page_number - 1) * page_size
            primary = {
                "total": len(all_groups),
                "courses": matched_courses,
                "groups": all_groups[start:start + page_size],
            }
        else:
            primary = self._search_courses_page(
                teaching_class_type=effective_scope, page_number=page_number,
                page_size=page_size, keyword=keyword, campus=campus,
                order_by=order_by, filters=remote_filters,
            )
        for item in primary["courses"]:
            item.setdefault("teaching_class_type", effective_scope)
        apply_selection_market_semantics(primary["courses"], batch.selection_type_code)
        tags_by_code: dict[str, set[str]] = {}
        label = (
            "所有课程" if effective_scope == "ALL"
            else "本轮课程" if effective_scope == "ROUND"
            else _COURSE_TYPE_NAMES.get(effective_scope, "其他课程")
        )
        if effective_scope not in {"ALL", "ROUND"}:
            for item in primary["courses"]:
                code = _text(item.get("course_code"))
                if code:
                    tags_by_code.setdefault(code, set()).add(label)
        scope_options = [
            {"code": "ALL", "name": "所有课程"},
            {"code": "ROUND", "name": "本轮课程"},
            *[_course_type_menu(item) for item in batch.menus],
        ]
        return {
            "_account": str(getattr(self.auth, "username", "") or ""),
            "_batch": batch.to_dict(),
            "total": primary["total"],
            "groups": [
                {**group, "source_tags": sorted(set(group.get("source_tags") or []) | tags_by_code.get(group.get("course_code", ""), set()))}
                for group in (primary.get("groups") or group_course_rows(primary["courses"], source_tags=tags_by_code))
            ],
            "scope": effective_scope,
            "scope_options": scope_options,
        }

    def get_catalog_filter_options(self, *, batch_code: str) -> dict[str, Any]:
        context = self.get_context()
        batch = next((item for item in context["batches"] if item.code == batch_code), None)
        if batch is None:
            raise JwxkError("选课轮次不存在或当前账号不可见")
        scope = next((item["code"] for item in batch.menus if item.get("code") != "ALLKC"), None) or (
            "ALLKC" if any(item.get("code") == "ALLKC" for item in batch.menus) else
            (batch.menus[0]["code"] if batch.menus else "ALLKC")
        )
        self._activate_batch(batch_code)
        # Keep this request short: it shares the authenticated remote-session lock
        # with the visible catalog.  The frontend progressively merges values from
        # every catalog page the user loads instead of blocking initial rendering
        # while scanning the entire official directory.
        courses = self._search_courses_page(
            teaching_class_type=scope, page_number=1, page_size=50,
        )["courses"]

        def options(values):
            return [{"value": value, "label": value} for value in sorted({_text(value) for value in values if _text(value)})]

        campus_options: dict[str, dict[str, str]] = {}
        current_campus = normalize_jwxk_campus_code(context.get("current_campus"))
        current_campus_name = jwxk_campus_label(current_campus, context.get("current_campus_name"))
        if current_campus:
            campus_options[current_campus] = {
                "value": current_campus,
                "label": current_campus_name or current_campus,
            }
        for course in courses:
            candidates = [{
                "code": course.get("campus"), "name": course.get("campus_name"),
            }, *[
                {"code": item.get("campus"), "name": item.get("campus_name")}
                for item in course.get("schedules") or []
            ]]
            for candidate in candidates:
                code = normalize_jwxk_campus_code(candidate.get("code"))
                name = jwxk_campus_label(code, candidate.get("name"))
                if code:
                    campus_options[code] = {
                        "value": code,
                        "label": name or campus_options.get(code, {}).get("label") or code,
                    }

        return {
            "scopes": [
                {"code": "ALL", "name": "所有课程"},
                {"code": "ROUND", "name": "本轮课程"},
                *[_course_type_menu(item) for item in batch.menus],
            ],
            "availability": [
                {"value": "selectable", "label": "本轮可选"},
                {"value": "available", "label": "仍有余量"},
                {"value": "conflict_free", "label": "官方无冲突"},
                {"value": "selected", "label": "已经选择"},
            ],
            "weekdays": [{"value": str(day), "label": f"周{label}"} for day, label in enumerate("一二三四五六日", 1)],
            "sections": [{"value": str(section), "label": f"第{section}节"} for section in range(1, 31)],
            "course_natures": options(course.get("course_nature") for course in courses),
            "course_categories": options(course.get("course_category") for course in courses),
            "general_elective_categories": options(
                course.get("general_elective_category") for course in courses
            ),
            "campuses": sorted(
                campus_options.values(), key=lambda item: item["label"]
            ),
            "departments": options(course.get("department") for course in courses),
        }

    def get_selection_schedule(self, *, batch_code: str) -> dict[str, Any]:
        result = self.get_selected(batch_code=batch_code)
        courses = result.get("selected") or result.get("volunteered") or []
        meetings = []
        for course in courses:
            for index, meeting in enumerate(course.get("schedules") or []):
                meetings.append({
                    **meeting,
                    "candidate_id": f"{course.get('class_id') or course.get('course_code')}:{index}",
                    "course_code": course.get("course_code", ""),
                    "course_name": course.get("course_name", ""),
                    "teaching_class_id": course.get("class_id", ""),
                    "teacher": meeting.get("teacher") or course.get("teacher", ""),
                    "location": meeting.get("location") or course.get("location", ""),
                    "campus": meeting.get("campus") or course.get("campus", ""),
                })
        return {
            "source": "selected_records_fallback",
            "source_label": "根据官方已选记录生成",
            "courses": courses,
            "meetings": meetings,
        }


class JwxkPublicClient:
    """Small allow-listed client for data available before authentication."""

    def __init__(self, *, timeout: int = 20, session: requests.Session | None = None):
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept-Language": "zh-CN,zh;q=0.9",
        })

    def get_batches(self, *, now: datetime | None = None) -> list[JwxkBatch]:
        response = self.session.get(JWXK_PROFILE_URL, timeout=self.timeout)
        response.raise_for_status()
        return parse_public_batches(response.text, now=now)


def resolve_network_mode(preference: str, primary_mode: str) -> str:
    if preference not in {"follow", "direct", "webvpn"}:
        raise ValueError("选课系统网络模式必须为 follow、direct 或 webvpn")
    if preference == "follow":
        return "webvpn" if primary_mode == "webvpn" else "direct"
    return preference
