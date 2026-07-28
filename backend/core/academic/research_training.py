"""科研训练（学生课题报名）API。"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional

from backend.core.auth import NEUAuthClient


class ResearchTrainingError(RuntimeError):
    """科研训练业务错误。"""


@dataclass
class ResearchBatch:
    batch_id: str
    name: str
    term_code: str
    term_name: str
    max_topics: int
    rank_limit_percent: float
    minimum_gpa: Optional[float]
    allow_failed_courses: bool
    allow_failed_courses_display: str
    minimum_journal_count: int


@dataclass
class ResearchEligibility:
    available: bool
    gpa: Optional[str]
    major_rank: Optional[str]
    reason: str = ""


@dataclass
class ResearchTopic:
    topic_id: str
    batch_id: str
    title: str
    project_name: str
    major: str
    college: str
    key_laboratory: bool
    key_laboratory_name: str
    advisor_id: str
    advisor_name: str
    advisor_college: str
    contact: str
    capacity: int
    registered_count: int
    confirmed_count: int
    registration_id: str
    registration_status_code: str
    registration_status: str
    rejection_reason: str
    application_reason: str

    @property
    def is_registered(self) -> bool:
        return bool(self.registration_id)

    @property
    def is_full(self) -> bool:
        return self.capacity > 0 and self.registered_count >= self.capacity

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["is_registered"] = self.is_registered
        data["is_full"] = self.is_full
        data["can_enroll"] = not self.is_registered and not self.is_full
        data["can_cancel"] = self.registration_status_code in {"-10", "10"}
        return data


class ResearchTrainingAPI:
    """封装教务系统 kyxlneu 应用的学生端接口。"""

    BASE_URL = "https://jwxt.neu.edu.cn/jwapp/sys/kyxlneu"
    PAGE_MODEL_URL = f"{BASE_URL}/modules/kyxl"
    DETAIL_URL = f"{BASE_URL}/modules/jsktgl/ktsbglcxlb.do"
    HEADERS = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": f"{BASE_URL}/*default/index.do",
    }

    def __init__(self, client: NEUAuthClient):
        self._client = client

    @staticmethod
    def _number(value: Any, default: float = 0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _integer(value: Any, default: int = 0) -> int:
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _rows(body: Dict[str, Any], action: str) -> List[Dict[str, Any]]:
        item = (body.get("datas") or {}).get(action)
        if isinstance(item, list):
            return item
        if isinstance(item, dict) and isinstance(item.get("rows"), list):
            return item["rows"]
        return []

    def _post_model(self, action: str, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        response = self._client.post(
            f"{self.PAGE_MODEL_URL}/{action}.do",
            data=data or {},
            headers=self.HEADERS,
        )
        body = response.json()
        if body.get("code") != "0":
            raise ResearchTrainingError(
                body.get("msg") or body.get("message") or f"{action} 请求失败"
            )
        return body

    @staticmethod
    def _query_rule(name: str, value: str, builder: str = "equal") -> Dict[str, str]:
        return {
            "name": name,
            "builder": builder,
            "linkOpt": "AND",
            "value": value,
        }

    def get_current_batch(self) -> ResearchBatch:
        body = self._post_model("cxdqxnxqpc")
        row = (body.get("datas") or {}).get("cxdqxnxqpc") or {}
        if not row:
            raise ResearchTrainingError("当前没有可用的科研训练报名批次")
        minimum_gpa = row.get("XSJD")
        return ResearchBatch(
            batch_id=str(row.get("WID") or ""),
            name=str(row.get("PCMC") or ""),
            term_code=str(row.get("XNXQDM") or ""),
            term_name=str(row.get("XNXQDM_DISPLAY") or ""),
            max_topics=self._integer(row.get("XSKXKTS")),
            rank_limit_percent=self._number(row.get("ZYPM")),
            minimum_gpa=self._number(minimum_gpa) if minimum_gpa not in (None, "") else None,
            allow_failed_courses=str(row.get("SFYXYBJGCJ") or "") == "1",
            allow_failed_courses_display=str(row.get("SFYXYBJGCJ_DISPLAY") or ""),
            minimum_journal_count=self._integer(row.get("ZBCS")),
        )

    def get_eligibility(self, batch_id: str) -> ResearchEligibility:
        body = self._post_model("cxxsjdjzypm", {"PCWID": batch_id})
        rows = self._rows(body, "cxxsjdjzypm")
        if not rows:
            return ResearchEligibility(
                available=False,
                gpa=None,
                major_rank=None,
                reason=(
                    "教务系统未返回平均绩点和专业排名，官方页面会因此无法打开报名表单。"
                    "请联系教务老师补全科研训练资格数据。"
                ),
            )
        row = rows[0]
        return ResearchEligibility(
            available=True,
            gpa=str(row.get("PJJD") or ""),
            major_rank=str(row.get("ZYPM") or ""),
        )

    @staticmethod
    def _topic_from_row(row: Dict[str, Any]) -> ResearchTopic:
        return ResearchTopic(
            topic_id=str(row.get("WID") or row.get("KTWID") or ""),
            batch_id=str(row.get("PCWID") or ""),
            title=str(row.get("YJTM") or ""),
            project_name=str(row.get("KYXMMC") or ""),
            major=str(row.get("SSZY_DISPLAY") or row.get("SSZY") or ""),
            college=str(row.get("SSYX_DISPLAY") or row.get("SSYX") or ""),
            key_laboratory=str(row.get("SFZDSYS") or "") == "1",
            key_laboratory_name=str(
                row.get("SSZDSYS_DISPLAY") or row.get("SSZDSYS") or ""
            ),
            advisor_id=str(row.get("SQR") or ""),
            advisor_name=str(row.get("DSXM") or row.get("SQRXM") or ""),
            advisor_college=str(
                row.get("DSDW_DISPLAY")
                or row.get("SSYX_DISPLAY")
                or row.get("DSDW")
                or ""
            ),
            contact=str(row.get("LXFS") or ""),
            capacity=ResearchTrainingAPI._integer(row.get("ZSXSS")),
            registered_count=ResearchTrainingAPI._integer(row.get("YBMRS")),
            confirmed_count=ResearchTrainingAPI._integer(row.get("YQRRS")),
            registration_id=str(row.get("BMWID") or ""),
            registration_status_code=str(row.get("BMZT") or ""),
            registration_status=str(row.get("BMZT_DISPLAY") or row.get("BMZT") or ""),
            rejection_reason=str(row.get("THLY") or ""),
            application_reason=str(row.get("SQLY") or ""),
        )

    def get_topics(
        self,
        batch_id: str,
        *,
        page: int = 1,
        page_size: int = 20,
        keyword: str = "",
        project_name: str = "",
        advisor_name: str = "",
    ) -> Dict[str, Any]:
        rules = [self._query_rule("PCWID", batch_id)]
        for field, value in (
            ("YJTM", keyword),
            ("KYXMMC", project_name),
            ("DSXM", advisor_name),
        ):
            value = value.strip()
            if value:
                rules.append(self._query_rule(field, value, "include"))
        body = self._post_model(
            "ktbmcxlb",
            {
                "querySetting": json.dumps(rules, ensure_ascii=False),
                "pageSize": page_size,
                "pageNumber": page,
            },
        )
        container = (body.get("datas") or {}).get("ktbmcxlb") or {}
        rows = self._rows(body, "ktbmcxlb")
        return {
            "topics": [self._topic_from_row(row).to_dict() for row in rows],
            "total": self._integer(container.get("totalSize"), len(rows)),
            "page": self._integer(container.get("pageNumber"), page),
            "page_size": self._integer(container.get("pageSize"), page_size),
        }

    def get_topic_detail(self, topic_id: str) -> Dict[str, Any]:
        response = self._client.post(
            self.DETAIL_URL,
            data={"WID": topic_id},
            headers=self.HEADERS,
        )
        body = response.json()
        if body.get("code") != "0":
            raise ResearchTrainingError(body.get("msg") or "获取课题详情失败")
        rows = self._rows(body, "ktsbglcxlb")
        if not rows:
            raise ResearchTrainingError("课题不存在或已不可见")
        row = rows[0]
        topic = self._topic_from_row(row).to_dict()
        topic.update(
            {
                "introduction": str(row.get("KTJJ") or ""),
                "requirements": str(row.get("BMYQ") or row.get("XSBMTJ") or ""),
                "advisor_email": str(row.get("DZYX") or ""),
                "advisor_title": str(row.get("ZC_DISPLAY") or row.get("ZC") or ""),
            }
        )
        return topic

    def get_confirmed_topics(self, batch_id: str) -> List[Dict[str, Any]]:
        rules = [self._query_rule("PCWID", batch_id)]
        body = self._post_model(
            "yqrktcxlb",
            {
                "querySetting": json.dumps(rules, ensure_ascii=False),
                "pageSize": 50,
                "pageNumber": 1,
            },
        )
        rows = self._rows(body, "yqrktcxlb")
        return [
            {
                "record_id": str(row.get("WID") or ""),
                "topic_id": str(row.get("KTWID") or ""),
                "batch_id": str(row.get("PCWID") or ""),
                "title": str(row.get("YJTM") or ""),
                "project_name": str(row.get("KYXMMC") or ""),
                "major": str(row.get("SSZY_DISPLAY") or row.get("SSZY") or ""),
                "college": str(row.get("SSYX_DISPLAY") or row.get("SSYX") or ""),
                "advisor_name": str(row.get("SQRXM") or row.get("DSXM") or ""),
                "advisor_contact": str(row.get("LXFS") or ""),
                "journal_count": self._integer(row.get("ZJS")),
                "score": str(row.get("CJ") or ""),
            }
            for row in rows
        ]

    @staticmethod
    def _normalize_rank(value: str) -> str:
        try:
            return f"{float(value):.2f}"
        except (TypeError, ValueError):
            return str(value or "")

    def enroll(
        self,
        topic_id: str,
        *,
        batch_id: str,
        phone: str,
        email: str,
        reason: str = "",
    ) -> Dict[str, Any]:
        eligibility = self.get_eligibility(batch_id)
        if not eligibility.available:
            raise ResearchTrainingError(eligibility.reason)
        form = {
            "LXDH": phone.strip(),
            "DZYX": email.strip(),
            "PJJD": eligibility.gpa or "",
            "PM": self._normalize_rank(eligibility.major_rank or ""),
            "SQLY": reason.strip(),
        }
        response = self._client.post(
            f"{self.BASE_URL}/api/kyxl/ktbm/save.do",
            data={
                "FORMJSON": json.dumps(form, ensure_ascii=False),
                "KTWID": topic_id,
            },
            headers=self.HEADERS,
        )
        body = response.json()
        if body.get("code") != "0":
            raise ResearchTrainingError(
                body.get("msg") or body.get("message") or "课题报名失败"
            )
        return {"success": True, "message": body.get("msg") or "报名成功"}

    def cancel_enrollment(self, topic_id: str) -> Dict[str, Any]:
        query_setting = json.dumps(
            [self._query_rule("WID", topic_id)],
            ensure_ascii=False,
        )
        response = self._client.post(
            f"{self.BASE_URL}/api/kyxl/ktbm/qxbm.do",
            data={"querySetting": query_setting},
            headers=self.HEADERS,
        )
        body = response.json()
        if body.get("code") != "0":
            raise ResearchTrainingError(
                body.get("msg") or body.get("message") or "取消报名失败"
            )
        return {"success": True, "message": body.get("msg") or "已取消报名"}
