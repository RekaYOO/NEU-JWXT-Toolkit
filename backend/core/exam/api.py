"""
NEU 教务系统考试安排 API

接口来源：jwxt.neu.edu.cn/jwapp/sys/homeapp
"""

import re
import logging
import uuid
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Any
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ExamInfo:
    """考试信息"""
    task_id: str
    course_name: str
    course_no: str
    course_desc: str
    exam_type: str
    exam_type_code: str
    exam_status: int          # 0=未开始, 1=进行中, 2=已结束
    exam_date: str            # yyyy-MM-dd 00:00:00
    exam_time_description: str
    week: int
    exam_place: str
    exam_seat_no: str
    teachers: str
    teaching_class_id: str
    start_time: Optional[str] = None
    end_time: Optional[str] = None


class ExamAPI:
    """考试安排 API"""

    BASE_URL = "https://jwxt.neu.edu.cn/jwapp/sys/homeapp"
    TERMS_URL = f"{BASE_URL}/api/home/kb/xnxq.do"
    EXAMS_URL = f"{BASE_URL}/api/home/student/exams.do"

    HEADERS = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Referer": "https://jwxt.neu.edu.cn/jwapp/sys/homeapp/home/index.html",
        "X-Requested-With": "XMLHttpRequest",
    }

    def __init__(self, auth_client):
        self._client = auth_client

    def get_terms(self) -> List[Dict[str, Any]]:
        """获取学期列表"""
        try:
            resp = self._client.session.get(self.TERMS_URL, headers=self.HEADERS, timeout=10)
            data = resp.json()
            if data.get("code") == "0":
                return data.get("datas", [])
        except Exception as e:
            logger.warning(f"获取学期列表失败: {e}")
        return []

    def get_current_term(self) -> Optional[str]:
        """获取当前学期代码"""
        terms = self.get_terms()
        for t in terms:
            if t.get("selected") is True:
                return t.get("itemCode")
        return terms[0].get("itemCode") if terms else None

    def get_exams(self, term_code: str = "") -> List[ExamInfo]:
        """获取指定学期的考试安排"""
        if not term_code:
            term_code = self.get_current_term()
        if not term_code:
            return []

        try:
            resp = self._client.session.post(
                self.EXAMS_URL,
                data={"termCode": term_code},
                headers=self.HEADERS,
                timeout=30,
            )
            data = resp.json()
            if data.get("code") == "0":
                return [self._parse_exam(item) for item in data.get("datas", [])]
        except Exception as e:
            logger.warning(f"获取考试安排失败: {e}")
        return []

    def _parse_exam(self, item: dict) -> ExamInfo:
        return ExamInfo(
            task_id=item.get("taskId", ""),
            course_name=item.get("courseName", ""),
            course_no=item.get("courseNo", ""),
            course_desc=item.get("courseDesc", ""),
            exam_type=item.get("examType", ""),
            exam_type_code=item.get("examTypeCode", ""),
            exam_status=int(item.get("examStatus", 0)),
            exam_date=item.get("examDate", ""),
            exam_time_description=item.get("examTimeDescription", ""),
            week=int(item.get("week", 0)),
            exam_place=item.get("examPlace", ""),
            exam_seat_no=str(item.get("examSeatNo", "")),
            teachers=item.get("teachers", ""),
            teaching_class_id=item.get("teachingClassId", ""),
            start_time=item.get("startTime"),
            end_time=item.get("endTime"),
        )

    def parse_exam_time(self, exam: ExamInfo) -> tuple:
        """
        从 examTimeDescription 解析开始和结束时间
        示例: '2026年05月08日 10:10-12:10(星期五第1场)'
        返回: (start_dt, end_dt) 或 (None, None)
        """
        desc = exam.exam_time_description
        if not desc:
            return None, None

        date_match = re.search(r"(\d{4})年(\d{2})月(\d{2})日", desc)
        time_match = re.search(r"(\d{2}:\d{2})-(\d{2}:\d{2})", desc)

        if date_match and time_match:
            date_str = f"{date_match.group(1)}-{date_match.group(2)}-{date_match.group(3)}"
            start_dt = datetime.strptime(f"{date_str} {time_match.group(1)}", "%Y-%m-%d %H:%M")
            end_dt = datetime.strptime(f"{date_str} {time_match.group(2)}", "%Y-%m-%d %H:%M")
            return start_dt, end_dt
        return None, None

    def generate_ics(self, exams: List[ExamInfo], student_name: str = "") -> str:
        """
        生成 ICS (iCalendar) 文件内容
        RFC 5545 格式
        """
        lines = [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//NEU-JWXT-Toolkit//Exam Schedule//ZH",
            "CALSCALE:GREGORIAN",
            "METHOD:PUBLISH",
            "X-WR-CALNAME:NEU 考试安排",
            "X-WR-TIMEZONE:Asia/Shanghai",
        ]

        if student_name:
            lines.append(f"X-WR-CALDESC:{student_name} 的考试安排")

        for exam in exams:
            start_dt, end_dt = self.parse_exam_time(exam)
            if not start_dt or not end_dt:
                continue

            uid = str(uuid.uuid4())
            dtstamp = datetime.now().strftime("%Y%m%dT%H%M%SZ")
            dtstart = start_dt.strftime("%Y%m%dT%H%M%S")
            dtend = end_dt.strftime("%Y%m%dT%H%M%S")

            summary = f"【{exam.exam_type}】{exam.course_name}"
            location = exam.exam_place
            description = (
                f"课程: {exam.course_name}\\n"
                f"类型: {exam.exam_type}\\n"
                f"时间: {exam.exam_time_description}\\n"
                f"地点: {exam.exam_place}\\n"
                f"座位: {exam.exam_seat_no}\\n"
                f"教师: {exam.teachers}\\n"
                f"课程号: {exam.course_no}"
            )

            lines.extend([
                "BEGIN:VEVENT",
                f"UID:{uid}",
                f"DTSTAMP:{dtstamp}",
                f"DTSTART;TZID=Asia/Shanghai:{dtstart}",
                f"DTEND;TZID=Asia/Shanghai:{dtend}",
                f"SUMMARY:{summary}",
                f"LOCATION:{location}",
                f"DESCRIPTION:{description}",
                "BEGIN:VALARM",
                "ACTION:DISPLAY",
                "DESCRIPTION:考试提醒",
                "TRIGGER:-PT24H",  # 提前24小时
                "END:VALARM",
                "BEGIN:VALARM",
                "ACTION:DISPLAY",
                "DESCRIPTION:考试即将开始",
                "TRIGGER:-PT2H",   # 提前2小时
                "END:VALARM",
                "END:VEVENT",
            ])

        lines.append("END:VCALENDAR")
        return "\r\n".join(lines)
