"""Read-only foundation for NEU's independent jwxk course-selection system.

The public profile page is the only source used before an authenticated batch
opens.  Course and mutation endpoints are deliberately not guessed here.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import json
import re
from typing import Any, Literal

import requests


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

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["course_types"] = list(self.course_types)
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
        ))
    return result


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
