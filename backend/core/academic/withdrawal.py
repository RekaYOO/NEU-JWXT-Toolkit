"""Official JWXT withdrawal-management API.

This is deliberately separate from the JWXK service.  The official page uses
the primary JWXT session and exposes a second, self-selected-course withdrawal
flow.  Reads are safe; the mutation is only called by the explicit endpoint
after the UI confirmation.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any


class OfficialWithdrawalError(RuntimeError):
    """The official withdrawal page returned an unusable response."""


@dataclass(frozen=True)
class OfficialWithdrawalCourse:
    wid: str
    course_serial: str
    course_code: str
    course_name: str
    teaching_class_id: str
    department: str
    teacher: str
    schedule: str
    course_nature: str
    course_category: str
    hours: float | None
    credits: float | None
    class_start_at: str
    withdrawal_start_at: str
    withdrawal_end_at: str
    source_code: str
    source_label: str
    weight: float | None
    can_withdraw: bool
    unavailable_reason: str
    penalty_phase: str
    penalty_label: str
    penalty_weight: int | None
    schedules: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "wid": self.wid,
            "course_serial": self.course_serial,
            "course_code": self.course_code,
            "course_name": self.course_name,
            "teaching_class_id": self.teaching_class_id,
            "department": self.department,
            "teacher": self.teacher,
            "schedule": self.schedule,
            "course_nature": self.course_nature,
            "course_category": self.course_category,
            "hours": self.hours,
            "credits": self.credits,
            "class_start_at": self.class_start_at,
            "withdrawal_start_at": self.withdrawal_start_at,
            "withdrawal_end_at": self.withdrawal_end_at,
            "source_code": self.source_code,
            "source_label": self.source_label,
            "weight": self.weight,
            "can_withdraw": self.can_withdraw,
            "unavailable_reason": self.unavailable_reason,
            "penalty_phase": self.penalty_phase,
            "penalty_label": self.penalty_label,
            "penalty_weight": self.penalty_weight,
            "schedules": self.schedules,
        }


class OfficialWithdrawalAPI:
    CURRENT_TERM_URL = (
        "https://jwxt.neu.edu.cn/jwapp/sys/wsxksz/modules/tkgl/"
        "cxdqxkxnxq.do"
    )
    COURSES_URL = (
        "https://jwxt.neu.edu.cn/jwapp/sys/wsxksz/modules/tkgl/"
        "cxxsktrw.do"
    )
    PENALTY_URL = (
        "https://jwxt.neu.edu.cn/jwapp/sys/wsxksz/modules/tkgl/"
        "cxxqkcbl.do"
    )
    WITHDRAW_URL = (
        "https://jwxt.neu.edu.cn/jwapp/sys/wsxksz/tkglController/"
        "toTk.do"
    )
    HEADERS = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
    }

    def __init__(self, auth_client):
        self._client = auth_client

    @staticmethod
    def _payload(response) -> dict[str, Any]:
        if int(getattr(response, "status_code", 200) or 200) >= 400:
            raise OfficialWithdrawalError("官方退课管理接口请求失败")
        try:
            payload = json.loads(response.content.decode("utf-8"))
        except Exception as error:
            raise OfficialWithdrawalError("官方退课管理响应格式异常") from error
        if not isinstance(payload, dict) or str(payload.get("code")) != "0":
            raise OfficialWithdrawalError("官方退课管理接口返回失败")
        return payload

    @classmethod
    def _rows(cls, payload: dict[str, Any], key: str) -> list[dict[str, Any]]:
        data = (payload.get("datas") or {}).get(key)
        if not isinstance(data, dict) or "rows" not in data:
            raise OfficialWithdrawalError("官方退课管理响应结构异常")
        rows = data.get("rows")
        if not isinstance(rows, list):
            raise OfficialWithdrawalError("官方退课管理响应结构异常")
        return [row for row in rows if isinstance(row, dict)]

    def get_current_term(self) -> dict[str, str]:
        payload = self._payload(self._client.get(
            self.CURRENT_TERM_URL,
            headers=self.HEADERS,
            timeout=(5, 20),
        ))
        rows = self._rows(payload, "cxdqxkxnxq")
        row = rows[0] if rows else {}
        return {
            "code": str(row.get("DM") or ""),
            "name": str(row.get("MC") or ""),
        }

    def get_penalty_rules(self, term_code: str) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for phase in ("1", "2"):
            try:
                payload = self._payload(self._client.get(
                    self.PENALTY_URL,
                    params={"PX": phase, "XNXQDM": term_code},
                    headers=self.HEADERS,
                    timeout=(5, 20),
                ))
                row = (self._rows(payload, "cxxqkcbl") or [{}])[0]
                result[phase] = {
                    "ratio": row.get("KCBL"),
                    "label": str(row.get("KCBLMC") or ""),
                }
            except Exception:
                # The list remains useful when the optional penalty lookup is
                # temporarily unavailable; the official page does the same.
                continue
        return result

    @staticmethod
    def _parse_time(value: Any) -> datetime | None:
        try:
            return datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S")
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _parse_schedule(value: Any, course: dict[str, Any]) -> list[dict[str, Any]]:
        """Keep official timetable text usable by the embedded timetable.

        The withdrawal page returns a display string rather than structured
        meetings.  Parse only unambiguous week/day/section fragments; unknown
        fragments remain in ``schedule`` and are never invented as meetings.
        """
        text = str(value or '').strip()
        if not text:
            return []
        chinese = '一二三四五六日天'
        chinese_numbers = {'一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9, '十': 10, '十一': 11, '十二': 12}
        result: list[dict[str, Any]] = []
        for index, fragment in enumerate(re.split(r'[，,；;\n]+', text)):
            fragment = fragment.strip()
            if not fragment:
                continue
            week_match = re.search(r'(\d{1,2})\s*(?:[-~～—至]\s*(\d{1,2}))?\s*周', fragment)
            day_match = re.search(r'(?:星期|周)\s*([一二三四五六日天])', fragment)
            section_match = re.search(r'第\s*([0-9]{1,2}|[一二三四五六七八九十]{1,3})\s*节(?:\s*[-~～—至]\s*第\s*([0-9]{1,2}|[一二三四五六七八九十]{1,3})\s*节)?', fragment)
            if not (week_match and day_match and section_match):
                continue
            def number(raw):
                raw = str(raw or '')
                return int(raw) if raw.isdigit() else chinese_numbers.get(raw)
            start_week, end_week = int(week_match.group(1)), int(week_match.group(2) or week_match.group(1))
            weekday = chinese.index('日' if day_match.group(1) == '天' else day_match.group(1)) + 1
            start_section, end_section = number(section_match.group(1)), number(section_match.group(2) or section_match.group(1))
            if not (1 <= start_week <= end_week <= 30 and 1 <= weekday <= 7 and start_section and end_section and start_section <= end_section):
                continue
            parts = [part.strip() for part in fragment.split('/')]
            result.append({
                'meeting_id': f"official-withdrawal-{course.get('WID') or index}-{index}",
                'course_code': str(course.get('KCH') or ''),
                'course_name': str(course.get('KCM') or ''),
                'teaching_class_id': str(course.get('JXBID') or course.get('WID') or ''),
                'weeks': list(range(start_week, end_week + 1)),
                'weekday': weekday,
                'start_section': start_section,
                'end_section': end_section,
                'teacher': str(course.get('SKJS') or ''),
                'location': parts[-1] if len(parts) >= 4 else '',
                'raw_text': fragment,
                'layer': 'preview',
            })
        return result

    def list_courses(self, term_code: str) -> tuple[list[OfficialWithdrawalCourse], dict[str, dict[str, Any]]]:
        if not term_code:
            raise OfficialWithdrawalError("当前学期为空")
        rows: list[dict[str, Any]] = []
        page_number = 1
        while True:
            payload = self._payload(self._client.get(
                self.COURSES_URL,
                params={
                    "XNXQDM": term_code,
                    "pageNumber": str(page_number),
                    "pageSize": "100",
                    "querySetting": "[]",
                    "*order": "+KCXH,+KCH,+KCM",
                },
                headers=self.HEADERS,
                timeout=(5, 20),
            ))
            page = (payload.get("datas") or {}).get("cxxsktrw") or {}
            current_rows = self._rows(payload, "cxxsktrw")
            rows.extend(current_rows)
            total = int(page.get("totalSize") or 0)
            if len(rows) >= total:
                break
            if not current_rows or page_number >= 20:
                raise OfficialWithdrawalError("官方退课列表未能完整读取")
            page_number += 1
        rules = self.get_penalty_rules(term_code)
        now = datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)
        courses: list[OfficialWithdrawalCourse] = []
        for row in rows:
            if not row.get("WID"):
                continue
            source_code = str(row.get("XKLY") or "")
            withdrawal_start = self._parse_time(row.get("TKKSSJ"))
            class_start = self._parse_time(row.get("KSSJ"))
            withdrawal_end = self._parse_time(row.get("DSXSKSSJ") or row.get("DWXSKSSJ"))
            phase = ""
            reason = ""
            can_withdraw = source_code == "01"
            if not can_withdraw:
                reason = "非自选课程不允许退课"
            elif withdrawal_start is not None and now < withdrawal_start:
                reason = "尚未到官方退课开始时间"
            elif class_start is None or withdrawal_end is None:
                reason = "退课时间暂不可确认"
            elif now < class_start:
                phase = "1"
            elif now <= withdrawal_end:
                phase = "2"
            else:
                reason = "已超过允许退课时间"
            penalty = rules.get(phase) or {}
            weight = float(row["QZZ"]) if row.get("QZZ") not in (None, "") else None
            ratio = penalty.get("ratio")
            penalty_weight = (
                int(weight * float(ratio) + 0.5)
                if weight is not None and ratio is not None and phase else None
            )
            courses.append(OfficialWithdrawalCourse(
                wid=str(row.get("WID") or ""),
                course_serial=str(row.get("KCXH") or ""),
                course_code=str(row.get("KCH") or ""),
                course_name=str(row.get("KCM") or ""),
                teaching_class_id=str(row.get("JXBID") or ""),
                department=str(row.get("KKDWDM_DISPLAY") or ""),
                teacher=str(row.get("SKJS") or ""),
                schedule=str(row.get("YPSJDD") or ""),
                course_nature=str(row.get("KCXZDM_DISPLAY") or ""),
                course_category=str(row.get("KCLBDM_DISPLAY") or ""),
                hours=float(row["XS"]) if row.get("XS") not in (None, "") else None,
                credits=float(row["XF"]) if row.get("XF") not in (None, "") else None,
                class_start_at=str(row.get("KSSJ") or ""),
                withdrawal_start_at=str(row.get("TKKSSJ") or ""),
                withdrawal_end_at=str(row.get("DSXSKSSJ") or row.get("DWXSKSSJ") or ""),
                source_code=source_code,
                source_label=str(row.get("XKLY_DISPLAY") or ""),
                weight=weight,
                can_withdraw=can_withdraw and not reason,
                unavailable_reason=reason,
                penalty_phase=phase,
                penalty_label=str(penalty.get("label") or ""),
                penalty_weight=penalty_weight,
                schedules=self._parse_schedule(row.get("YPSJDD"), row),
            ))
        return courses, rules

    def withdraw(self, term_code: str, wid: str) -> dict[str, Any]:
        if not term_code or not wid:
            raise OfficialWithdrawalError("退课参数无效")
        query_setting = json.dumps([{
            "name": "WID",
            "value": wid,
            "linkOpt": "and",
            "builder": "m_value_equal",
        }], ensure_ascii=False, separators=(",", ":"))
        payload = self._payload(self._client.post(
            self.WITHDRAW_URL,
            data={"querySetting": query_setting, "XNXQDM": term_code},
            headers=self.HEADERS,
            timeout=(5, 20),
            retry_on_auth=False,
            retry_on_transport=False,
        ))
        return {"message": str(payload.get("msg") or "官方退课请求已提交")}
