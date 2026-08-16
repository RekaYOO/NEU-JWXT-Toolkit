"""Read-only client for NEU's official ``kbapp`` timetable application."""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from collections import OrderedDict
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from typing import Any, Dict, Iterable, List, Mapping, Optional

from backend.core.auth.client import NEULoginError


class TimetableError(RuntimeError):
    """The official timetable service returned an unusable response."""


@dataclass(frozen=True)
class TimetableTerm:
    code: str
    name: str
    current: bool = False


@dataclass(frozen=True)
class TimetableCampus:
    code: str
    name: str


@dataclass(frozen=True)
class TimetableWeek:
    number: int
    name: str
    start_date: str = ""
    end_date: str = ""
    current: bool = False


@dataclass(frozen=True)
class TimetableSection:
    number: int
    name: str
    start_time: str = ""
    end_time: str = ""


MODE_CONFIG: Dict[str, Dict[str, str]] = {
    "class": {"schedule_type": "05", "action": "bjlb"},
    "teacher": {"schedule_type": "02", "action": "lslb"},
    "room": {"schedule_type": "01", "action": "jslb"},
}

TARGET_FILTER_FIELDS: Dict[str, Dict[str, tuple[str, str]]] = {
    "class": {
        "grade": ("BJMC", "include"),
        "college": ("YXDM", "equal"),
        "major": ("ZYDM", "equal"),
        "direction": ("ZYFXDM", "equal"),
        "campus": ("XXXQDM", "equal"),
        "has_schedule": ("SFPK", "equal"),
    },
    "teacher": {
        "department": ("SZDWDM", "equal"),
        "title": ("ZCDM", "equal"),
        "gender": ("XBDM", "equal"),
        "external": ("SFWP", "equal"),
        "has_schedule": ("SFPK", "equal"),
    },
    "room": {
        "campus": ("XXXQDM", "equal"),
        "building": ("JXLDM", "equal"),
        "floor": ("LC", "equal"),
        "room_type": ("JASLXDM", "equal"),
        "department": ("GLDWDM", "equal"),
        "use_scope": ("SYFWDM", "equal"),
        "lab_center": ("SSSYZXDM", "equal"),
        "min_capacity": ("RL", "moreEqual"),
        "max_capacity": ("RL", "lessEqual"),
        "has_schedule": ("SFPK", "equal"),
    },
}

ALL_CAMPUSES_CODE = "all"


class TimetableAPI:
    """Official timetable queries through the shared authenticated client."""

    BASE_URL = "https://jwxt.neu.edu.cn/jwapp/sys"
    TERMS_URL = f"{BASE_URL}/jwpubapp/modules/zdgl/xnxqcx.do"
    CURRENT_TERM_URL = f"{BASE_URL}/jwpubapp/modules/gg/cxmrxnxq.do"
    WEEKS_URL = f"{BASE_URL}/kbbpapp/api/schoolCalendar/getTermWeeks.do"
    PERSONAL_CAMPUS_URL = f"{BASE_URL}/kbapp/api/wdkbcx/getMyScheduledCampus.do"
    TARGET_CAMPUS_URL = f"{BASE_URL}/kbapp/api/qxkbcx/getMyScheduledCampus.do"
    PERSONAL_SECTIONS_URL = f"{BASE_URL}/kbapp/api/wdkbcx/getMySectionList.do"
    SECTIONS_URL = f"{BASE_URL}/kbbpapp/api/kbck/getSectionList.do"
    PERSONAL_SCHEDULE_URL = f"{BASE_URL}/kbapp/api/wdkbcx/getMyScheduleDetail.do"
    TARGET_SCHEDULE_URL = f"{BASE_URL}/kbapp/api/qxkbcx/getScheduleDetail.do"
    TARGET_MODEL_URL = f"{BASE_URL}/kbapp/modules/qxkbcx"
    FILTER_CATALOG_PAGE_SIZE = 1000
    FILTER_CATALOG_MAX_PAGES = 400
    FILTER_CATALOG_MAX_ROWS = 20_000
    FILTER_CATALOG_MAX_CACHE_ENTRIES = 12
    FILTER_CATALOG_TTL_SECONDS = 30 * 60
    TERMS_CACHE_TTL_SECONDS = 10 * 60

    HEADERS = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Referer": f"{BASE_URL}/kbapp/*default/index.do",
        "X-Requested-With": "XMLHttpRequest",
    }

    def __init__(self, auth_client: Any):
        self._client = auth_client
        self._terms_cache: Optional[tuple[float, List[Dict[str, Any]]]] = None
        self._filter_catalog_cache: OrderedDict[
            tuple[Any, ...], tuple[float, Dict[str, Any]]
        ] = OrderedDict()

    @staticmethod
    def _integer(value: Any, default: int = 0) -> int:
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _text(value: Any) -> str:
        return str(value or "").strip()

    @staticmethod
    def _boolean(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value != 0
        return str(value or "").strip().lower() in {"1", "true", "yes", "y", "是"}

    @classmethod
    def _response_json(cls, response: Any, operation: str) -> Any:
        if int(getattr(response, "status_code", 200) or 200) >= 400:
            raise TimetableError(f"{operation}失败")
        try:
            body = response.json()
        except Exception as error:
            raise TimetableError(f"{operation}响应不是有效 JSON") from error
        if isinstance(body, dict):
            code = body.get("code")
            if code not in (None, 0, "0"):
                raise TimetableError(
                    cls._text(body.get("msg") or body.get("message")) or f"{operation}失败"
                )
        return body

    def _post(self, url: str, data: Optional[Mapping[str, Any]], operation: str) -> Any:
        try:
            response = self._client.post(
                url,
                data=dict(data or {}),
                headers=self.HEADERS,
                timeout=30,
            )
            return self._response_json(response, operation)
        except TimetableError:
            raise
        except NEULoginError:
            raise
        except Exception as error:
            raise TimetableError(f"{operation}失败") from error

    @classmethod
    def _find_rows(cls, body: Any, preferred: str = "") -> List[Dict[str, Any]]:
        if isinstance(body, list):
            return [item for item in body if isinstance(item, dict)]
        if not isinstance(body, dict):
            return []
        datas = body.get("datas", body)
        candidates: List[Any] = []
        if preferred and isinstance(datas, dict):
            candidates.append(datas.get(preferred))
        candidates.append(datas)
        if isinstance(datas, dict):
            candidates.extend(datas.values())
        for candidate in candidates:
            if isinstance(candidate, list):
                return [item for item in candidate if isinstance(item, dict)]
            if isinstance(candidate, dict) and isinstance(candidate.get("rows"), list):
                return [item for item in candidate["rows"] if isinstance(item, dict)]
        return []

    @classmethod
    def _container(cls, body: Any, preferred: str) -> Mapping[str, Any]:
        if not isinstance(body, dict):
            return {}
        datas = body.get("datas")
        if isinstance(datas, dict) and isinstance(datas.get(preferred), dict):
            return datas[preferred]
        if isinstance(datas, dict):
            for value in datas.values():
                if isinstance(value, dict) and isinstance(value.get("rows"), list):
                    return value
        return {}

    @staticmethod
    def _copy_terms(terms: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
        return [dict(term) for term in terms]

    def get_cached_terms(self) -> Optional[List[Dict[str, Any]]]:
        """Return a short-lived in-process term catalog without remote access."""
        cached = self._terms_cache
        if cached is None:
            return None
        saved_at, terms = cached
        if time.monotonic() - saved_at >= self.TERMS_CACHE_TTL_SECONDS:
            self._terms_cache = None
            return None
        return self._copy_terms(terms)

    def get_terms(self) -> List[Dict[str, Any]]:
        cached = self.get_cached_terms()
        if cached is not None:
            return cached
        current_body = self._post(
            self.CURRENT_TERM_URL,
            {"CSDM": "SYS", "ZCSDM": "DQXNXQDM"},
            "获取当前学期",
        )
        current_rows = self._find_rows(current_body, "cxmrxnxq")
        current_code = self._text(
            (current_rows[0] if current_rows else {}).get("XNXQDM")
            or (current_rows[0] if current_rows else {}).get("DM")
            or (current_rows[0] if current_rows else {}).get("CSZA")
        )
        if not current_code:
            container = (
                current_body.get("datas", current_body)
                if isinstance(current_body, dict)
                else current_body
            )
            preferred = (
                container.get("cxmrxnxq")
                if isinstance(container, dict)
                else container
            )
            if isinstance(preferred, str):
                current_code = self._text(preferred)
            elif isinstance(preferred, dict):
                current_code = self._text(
                    preferred.get("XNXQDM")
                    or preferred.get("DM")
                    or preferred.get("CSZA")
                    or preferred.get("value")
                )
            if not current_code and isinstance(container, dict):
                current_code = self._text(
                    container.get("XNXQDM")
                    or container.get("DQXNXQDM")
                    or container.get("currentTerm")
                    or container.get("value")
                )
        body = self._post(
            self.TERMS_URL,
            {"SFSY": "1", "*order": "+PX,-DM", "pageSize": 200, "pageNumber": 1},
            "获取学期列表",
        )
        terms: List[TimetableTerm] = []
        for row in self._find_rows(body, "xnxqcx"):
            code = self._text(row.get("DM") or row.get("XNXQDM"))
            if not code:
                continue
            terms.append(
                TimetableTerm(
                    code=code,
                    name=self._text(row.get("MC") or row.get("XNXQMC") or row.get("DM")),
                    current=code == current_code,
                )
            )
        if not terms and current_code:
            row = current_rows[0]
            terms.append(
                TimetableTerm(
                    current_code,
                    self._text(row.get("XNXQMC") or row.get("MC") or current_code),
                    True,
                )
            )
        if not terms:
            raise TimetableError("教务系统未返回可用学期")
        result = [asdict(term) for term in terms]
        self._terms_cache = (time.monotonic(), self._copy_terms(result))
        return self._copy_terms(result)

    def get_weeks(self, term_code: str) -> List[Dict[str, Any]]:
        body = self._post(self.WEEKS_URL, {"XNXQDM": term_code}, "获取教学周")
        weeks: List[TimetableWeek] = []
        for index, row in enumerate(self._find_rows(body), start=1):
            number = self._integer(
                row.get("serialNumber") or row.get("ZC") or row.get("DM"), index
            )
            start_date = self._text(
                row.get("startTime")
                or row.get("startDate")
                or row.get("beginDate")
                or row.get("KSRQ")
            )[:10]
            end_date = self._text(
                row.get("endTime")
                or row.get("endDate")
                or row.get("finishDate")
                or row.get("JSRQ")
            )[:10]
            start_date, end_date = self._normalize_teaching_week(start_date, end_date)
            weeks.append(
                TimetableWeek(
                    number=number,
                    name=self._text(row.get("name") or row.get("MC")) or f"第{number}周",
                    start_date=start_date,
                    end_date=end_date,
                    current=self._boolean(
                        row.get("curWeek")
                        if row.get("curWeek") is not None
                        else row.get("current")
                    ),
                )
            )
        return [asdict(week) for week in weeks]

    @staticmethod
    def _normalize_teaching_week(start_text: str, end_text: str) -> tuple[str, str]:
        """Normalize official Monday-Sunday ranges to the local Sunday-Saturday week."""
        try:
            start = date.fromisoformat(start_text)
            end = date.fromisoformat(end_text)
        except (TypeError, ValueError):
            return start_text, end_text
        if (end - start).days == 6 and start.weekday() == 0 and end.weekday() == 6:
            start -= timedelta(days=1)
            end -= timedelta(days=1)
        return start.isoformat(), end.isoformat()

    def get_campuses(
        self,
        term_code: str,
        *,
        mode: str = "personal",
        target_id: str = "",
    ) -> List[Dict[str, str]]:
        if mode == "personal":
            url = self.PERSONAL_CAMPUS_URL
            params: Dict[str, Any] = {"XNXQDM": term_code}
        else:
            config = self._mode_config(mode)
            url = self.TARGET_CAMPUS_URL
            params = {
                "XNXQDM": term_code,
                "CODE": target_id,
                "KBLX": config["schedule_type"],
            }
        body = self._post(url, params, "获取开课校区")
        campuses: List[TimetableCampus] = []
        for row in self._find_rows(body):
            code = self._text(row.get("id") or row.get("DM") or row.get("XQDM"))
            if code:
                campuses.append(
                    TimetableCampus(
                        code=code,
                        name=self._text(row.get("name") or row.get("MC") or row.get("XQMC")) or code,
                    )
                )
        # The official service may publish a future personal timetable before
        # getMyScheduledCampus starts returning its campus classification.  The
        # schedule endpoint already accepts an empty XQDM and returns the full
        # timetable, so an empty campus catalog is not evidence of "no class".
        if mode == "personal" and not campuses:
            campuses.append(TimetableCampus(code=ALL_CAMPUSES_CODE, name="全部校区"))
        needs_all_campuses = mode == "personal" and bool(campuses)
        if (needs_all_campuses or len(campuses) > 1) and not any(
            campus.code == ALL_CAMPUSES_CODE for campus in campuses
        ):
            campuses.insert(0, TimetableCampus(code=ALL_CAMPUSES_CODE, name="全部校区"))
        return [asdict(campus) for campus in campuses]

    def get_sections(
        self,
        term_code: str,
        *,
        mode: str = "personal",
        campus_code: str = "",
    ) -> List[Dict[str, Any]]:
        official_campus_code = "" if campus_code == ALL_CAMPUSES_CODE else campus_code
        if mode == "personal":
            url = self.PERSONAL_SECTIONS_URL
            params: Dict[str, Any] = {"XNXQDM": term_code, "XQDM": official_campus_code}
        else:
            config = self._mode_config(mode)
            url = self.SECTIONS_URL
            params = {
                "XNXQDM": term_code,
                "XQDM": official_campus_code,
                "KBLX": config["schedule_type"],
            }
        body = self._post(url, params, "获取课表节次")
        sections: List[TimetableSection] = []
        for index, row in enumerate(self._find_rows(body), start=1):
            number = self._integer(
                row.get("sectionNumber")
                or row.get("code")
                or row.get("JC")
                or row.get("DM"),
                index,
            )
            sections.append(
                TimetableSection(
                    number=number,
                    name=self._text(row.get("name") or row.get("MC")) or f"第{number}节",
                    start_time=self._text(row.get("startTime") or row.get("KSSJ")),
                    end_time=self._text(row.get("endTime") or row.get("JSSJ")),
                )
            )
        return [asdict(section) for section in sections]

    @staticmethod
    def _mode_config(mode: str) -> Dict[str, str]:
        config = MODE_CONFIG.get(mode)
        if not config:
            raise TimetableError("不支持的课表查询类型")
        return config

    @staticmethod
    def _query_rule(name: str, value: str, builder: str = "include") -> Dict[str, str]:
        return {"name": name, "builder": builder, "linkOpt": "AND", "value": value}

    @staticmethod
    def _keyword_fields(mode: str, keyword: str) -> List[str]:
        """Return bounded official model fields in lookup order.

        The teacher list returned by different kbapp deployments uses ``XM``
        for the searchable name even though some responses expose the same
        value as ``JSMC``.  EMap may silently ignore an unknown query field, so
        a single hard-coded field can look successful while merely returning
        the unfiltered first page.
        """
        code_like = bool(re.fullmatch(r"[A-Za-z0-9_-]+", keyword))
        name_fields = {
            "class": ["BJMC"],
            "teacher": ["XM", "JSMC"],
            "room": ["JASMC"],
        }[mode]
        if code_like and mode != "room":
            return ["CODE", *name_fields]
        if code_like:
            return [*name_fields, "CODE"]
        return name_fields

    @staticmethod
    def _target_matches_keyword(
        mode: str,
        row: Mapping[str, Any],
        target: Mapping[str, Any],
        keyword: str,
    ) -> bool:
        needle = keyword.strip().casefold()
        if not needle:
            return True
        row_values = {
            "class": (row.get("CODE"), row.get("BJMC"), row.get("MC")),
            "teacher": (
                row.get("CODE"), row.get("JGH"), row.get("JSBH"),
                row.get("XM"), row.get("JSMC"), row.get("MC"),
            ),
            "room": (row.get("CODE"), row.get("JASMC"), row.get("MC")),
        }[mode]
        return any(
            needle in str(value or "").casefold()
            for value in (*row_values, target.get("id"), target.get("name"))
        )

    def search_targets(
        self,
        mode: str,
        term_code: str,
        *,
        keyword: str = "",
        page: int = 1,
        page_size: int = 20,
        filters: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        config = self._mode_config(mode)
        normalized_keyword = keyword.strip()
        filter_rules = []
        for key, value in (filters or {}).items():
            if value in (None, "") or key not in TARGET_FILTER_FIELDS[mode]:
                continue
            field_name, builder = TARGET_FILTER_FIELDS[mode][key]
            normalized = {"yes": "1", "no": "0"}.get(str(value), str(value))
            filter_rules.append(self._query_rule(field_name, normalized, builder))
        action = config["action"]
        keyword_fields = self._keyword_fields(mode, normalized_keyword) if normalized_keyword else [""]
        body: Mapping[str, Any] = {}
        rows: List[Mapping[str, Any]] = []
        targets: List[Dict[str, Any]] = []
        all_rows_match = True
        for keyword_field in keyword_fields:
            rules = list(filter_rules)
            if keyword_field:
                rules.insert(0, self._query_rule(keyword_field, normalized_keyword))
            body = self._post(
                f"{self.TARGET_MODEL_URL}/{action}.do",
                {
                    "XNXQDM": term_code,
                    "querySetting": json.dumps(rules, ensure_ascii=False),
                    "pageNumber": page,
                    "pageSize": page_size,
                },
                "搜索课表对象",
            )
            rows = self._find_rows(body, action)
            row_targets = [
                (row, target)
                for row in rows
                for target in [self._target_from_row(mode, row)]
                if target["id"]
            ]
            targets = [target for _, target in row_targets]
            if not normalized_keyword:
                break
            matching_targets = [
                target for row, target in row_targets
                if self._target_matches_keyword(mode, row, target, normalized_keyword)
            ]
            if matching_targets:
                all_rows_match = len(matching_targets) == len(targets)
                targets = matching_targets
                break
            # An empty response may be a valid miss; a non-empty response with
            # no public match means EMap ignored this field.  In both cases try
            # the next documented response-field variant when one exists.
            targets = []
        container = self._container(body, action)
        remote_total = self._integer(container.get("totalSize"), len(targets))
        if normalized_keyword and not targets:
            remote_total = 0
        return {
            "items": targets,
            # When EMap mixed unrelated rows into a fuzzy result, expose only
            # the verified matches and do not invite endless pagination through
            # an unfiltered list.
            "total": remote_total if all_rows_match else len(targets),
            "page": self._integer(container.get("pageNumber"), page),
            "page_size": self._integer(container.get("pageSize"), page_size),
        }

    def get_target_filter_options(
        self,
        mode: str,
        term_code: str,
        *,
        keys: Optional[List[str]] = None,
        filters: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Return a complete, short-lived classification catalog for one target mode.

        The official application exposes classification values only on target rows.
        Fetching just the first search page therefore makes valid colleges, majors,
        buildings, and other categories impossible to select.  We scan the bounded
        official list once and cache only the de-identified category pairs in memory.
        """
        self._mode_config(mode)
        requested_keys = [key for key in (keys or []) if key in TARGET_FILTER_FIELDS[mode]]
        active_filters = {
            key: value for key, value in (filters or {}).items()
            if value not in (None, "") and key in TARGET_FILTER_FIELDS[mode]
        }
        # Without an explicit key list retain compatibility with the old endpoint.
        # The UI now requests only the next dependent level(s).
        option_keys = requested_keys or [
            key for key in TARGET_FILTER_FIELDS[mode]
            if key not in {"has_schedule", "min_capacity", "max_capacity"}
        ]
        # 保留无条件全量目录的旧键格式，兼容已有缓存与测试；只有按字段/条件
        # 请求的目录才使用扩展键，避免不同级联结果互相覆盖。
        cache_key = (
            (mode, term_code)
            if not requested_keys and not active_filters
            else (mode, term_code, tuple(sorted(option_keys)), tuple(sorted(active_filters.items())))
        )
        now = time.monotonic()
        expired_keys = [
            key
            for key, (saved_at, _) in self._filter_catalog_cache.items()
            if now - saved_at >= self.FILTER_CATALOG_TTL_SECONDS
        ]
        for key in expired_keys:
            self._filter_catalog_cache.pop(key, None)
        cached = self._filter_catalog_cache.get(cache_key)
        if cached:
            self._filter_catalog_cache.move_to_end(cache_key)
            return cached[1]

        option_maps: Dict[str, Dict[str, str]] = {
            key: {}
            for key in option_keys
            if key not in {"has_schedule", "min_capacity", "max_capacity"}
        }
        relation_keys: set[tuple[tuple[str, str], ...]] = set()
        page = 1
        total_pages = 1
        scanned_rows = 0
        while page <= total_pages and page <= self.FILTER_CATALOG_MAX_PAGES:
            payload = self.search_targets(
                mode,
                term_code,
                page=page,
                page_size=self.FILTER_CATALOG_PAGE_SIZE,
                filters=active_filters,
            )
            returned_page = max(self._integer(payload.get("page"), page), 1)
            if returned_page != page:
                raise TimetableError("课表分类目录分页未按请求推进")
            reported_total = self._integer(payload.get("total"))
            if not payload["items"] and (page > 1 or reported_total > 0):
                raise TimetableError("课表分类目录在读取完成前返回空页")
            scanned_rows += len(payload["items"])
            if scanned_rows > self.FILTER_CATALOG_MAX_ROWS:
                raise TimetableError("课表查询对象数量超过安全分类目录上限")
            for target in payload["items"]:
                # 选项可以按需返回，但级联筛选关系必须保留完整字段。
                # 例如请求 building 时仍要保留 campus，前端才能判断
                # “浑南校区 -> 对应教学楼”，否则会把所有教学楼误判为空。
                relation = {
                    key: str(value)
                    for key, value in target.get("filter_values", {}).items()
                    if key in TARGET_FILTER_FIELDS[mode] and value
                }
                if relation:
                    relation_keys.add(tuple(sorted(relation.items())))
                for key, value in target.get("filter_values", {}).items():
                    detail_key = "type" if key == "room_type" else key
                    label = target.get("details", {}).get(detail_key, "") or value
                    if key in option_maps and value and label:
                        option_maps[key].setdefault(str(value), str(label))
            total = max(reported_total, len(payload["items"]))
            effective_page_size = max(
                self._integer(payload.get("page_size"), self.FILTER_CATALOG_PAGE_SIZE),
                1,
            )
            reported_total_pages = max(1, math.ceil(total / effective_page_size))
            total_pages = max(total_pages, reported_total_pages)
            if page == 1:
                if total > self.FILTER_CATALOG_MAX_ROWS:
                    raise TimetableError("课表查询对象数量超过安全分类目录上限")
            if total > self.FILTER_CATALOG_MAX_ROWS or total_pages > self.FILTER_CATALOG_MAX_PAGES:
                raise TimetableError("课表查询对象数量超过安全分类目录上限")
            page += 1

        options = {
            key: [
                {"value": value, "label": label}
                for value, label in sorted(
                    values.items(),
                    key=lambda item: (item[1], item[0]),
                )
            ]
            for key, values in option_maps.items()
        }
        catalog = {
            "options": options,
            "relations": [dict(items) for items in sorted(relation_keys)],
        }
        self._filter_catalog_cache[cache_key] = (time.monotonic(), catalog)
        self._filter_catalog_cache.move_to_end(cache_key)
        while len(self._filter_catalog_cache) > self.FILTER_CATALOG_MAX_CACHE_ENTRIES:
            self._filter_catalog_cache.popitem(last=False)
        return catalog

    @classmethod
    def _target_from_row(cls, mode: str, row: Mapping[str, Any]) -> Dict[str, Any]:
        target_id = cls._text(
            row.get("CODE")
            or (row.get("JGH") if mode == "teacher" else None)
            or (row.get("JSBH") if mode == "teacher" else None)
            or row.get("DM")
            or row.get("WID")
        )
        if mode == "room":
            name = cls._text(row.get("JASMC") or row.get("MC") or target_id)
            details = {
                "campus": cls._text(row.get("XXXQDM_DISPLAY") or row.get("XXXQMC")),
                "building": cls._text(row.get("JXLDM_DISPLAY") or row.get("JXLMC")),
                "floor": cls._text(row.get("LC")),
                "type": cls._text(row.get("JASLXDM_DISPLAY") or row.get("JASLXMC")),
                "department": cls._text(row.get("GLDWDM_DISPLAY") or row.get("GLDWMC")),
                "use_scope": cls._text(row.get("SYFWDM_DISPLAY") or row.get("SYFW")),
                "lab_center": cls._text(row.get("SSSYZXDM_DISPLAY") or row.get("SSSYZX")),
                "capacity": cls._text(row.get("RL")),
            }
            filter_values = {
                "campus": cls._text(row.get("XXXQDM")),
                "building": cls._text(row.get("JXLDM")),
                "floor": cls._text(row.get("LCDM") or row.get("LC")),
                "room_type": cls._text(row.get("JASLXDM")),
                "department": cls._text(row.get("GLDWDM")),
                "use_scope": cls._text(row.get("SYFWDM")),
                "lab_center": cls._text(row.get("SSSYZXDM")),
            }
        elif mode == "teacher":
            name = cls._text(row.get("JSMC") or row.get("XM") or row.get("MC") or target_id)
            details = {
                "department": cls._text(row.get("SZDWDM_DISPLAY") or row.get("SZDWMC")),
                "title": cls._text(row.get("ZCDM_DISPLAY") or row.get("ZCMC")),
                "gender": cls._text(row.get("XBDM_DISPLAY") or row.get("XB")),
                "external": cls._text(row.get("SFWP_DISPLAY") or row.get("SFWP")),
            }
            filter_values = {
                "department": cls._text(row.get("SZDWDM")),
                "title": cls._text(row.get("ZCDM")),
                "gender": cls._text(row.get("XBDM")),
                "external": cls._text(row.get("SFWP")),
            }
        else:
            name = cls._text(row.get("BJMC") or row.get("MC") or target_id)
            grade_match = re.search(r"(?<!\d)(\d{2})\d{2}(?!\d)", name)
            grade_code = grade_match.group(1) if grade_match else ""
            details = {
                "grade": cls._text(row.get("NJDM_DISPLAY") or row.get("NJMC"))
                or (f"20{grade_code}级" if grade_code else ""),
                "college": cls._text(row.get("YXDM_DISPLAY") or row.get("YXMC")),
                "major": cls._text(row.get("ZYDM_DISPLAY") or row.get("ZYMC")),
                "direction": cls._text(row.get("ZYFXDM_DISPLAY") or row.get("ZYFXMC")),
                "campus": cls._text(row.get("XXXQDM_DISPLAY") or row.get("XXXQMC")),
            }
            filter_values = {
                "grade": cls._text(row.get("NJDM")) or grade_code,
                "college": cls._text(row.get("YXDM")),
                "major": cls._text(row.get("ZYDM")),
                "direction": cls._text(row.get("ZYFXDM")),
                "campus": cls._text(row.get("XXXQDM")),
            }
        return {
            "id": target_id,
            "name": name,
            "has_schedule": cls._text(row.get("SFPK_DISPLAY") or row.get("SFPK")),
            "details": {key: value for key, value in details.items() if value},
            "filter_values": {key: value for key, value in filter_values.items() if value},
        }

    @classmethod
    def _detail_texts(cls, value: Any) -> List[str]:
        if not isinstance(value, list):
            return []
        texts: List[str] = []
        for item in value:
            text = cls._text(item.get("text") if isinstance(item, dict) else item)
            if text:
                texts.append(text)
        return texts

    @classmethod
    def _course_from_row(cls, row: Mapping[str, Any], index: int) -> Dict[str, Any]:
        cell_details = cls._detail_texts(row.get("cellDetail"))
        title_details = cls._detail_texts(row.get("titleDetail"))
        tags = cls._detail_texts(row.get("tags"))
        selection_status_text = " ".join(filter(None, [
            *tags,
            cls._text(row.get("selectionStatus")),
            cls._text(row.get("courseStatus")),
            cls._text(row.get("XKZT_DISPLAY")),
            cls._text(row.get("ZT_DISPLAY")),
        ]))
        preselected = "预选" in selection_status_text
        course_name = cls._text(
            row.get("courseName") or row.get("KCM") or row.get("name")
        ) or (cell_details[0] if cell_details else "未命名课程")
        course_code = cls._text(
            row.get("courseCode") or row.get("courseNo") or row.get("KCH")
        )
        teaching_class_id = cls._text(
            row.get("teachClassId") or row.get("teachingClassId") or row.get("JXBID")
        )
        digest_source = "\x1f".join(
            [
                course_name,
                course_code,
                teaching_class_id,
                cls._text(row.get("dayOfWeek")),
                cls._text(row.get("beginSection")),
                cls._text(row.get("endSection")),
                "\x1e".join(title_details),
            ]
        )
        identifier = teaching_class_id or hashlib.sha256(
            digest_source.encode("utf-8")
        ).hexdigest()[:20]
        detail_lines = [*title_details, *cell_details, *tags]
        detail_text = " ".join(detail_lines)
        course_nature = next(
            (label for label in ("必修", "选修") if label in detail_text),
            "",
        )
        assessment_type = next(
            (label for label in ("考试", "考查") if label in detail_text),
            "",
        )
        grading_scheme = ""
        for line in detail_lines:
            match = re.search(r"(?:考试|考查)\s*[/／·|]\s*([^\s/／·|]+)", line)
            if match:
                grading_scheme = match.group(1).strip()
                break
        location = cls._text(
            row.get("location")
            or row.get("place")
            or row.get("roomName")
            or row.get("classroom")
            or row.get("JASMC")
        )
        campus = cls._text(
            row.get("campus") or row.get("campusName") or row.get("XQMC")
        )
        if not location:
            for line in title_details:
                campus_match = re.search(r"([^\s，,]*校区)\s+(.+)$", line)
                if campus_match:
                    campus = campus or campus_match.group(1).strip()
                    location = f"{campus_match.group(1).strip()} {campus_match.group(2).strip()}"
                    break
                tokens = [token.strip("，,。；;") for token in re.split(r"\s+", line) if token]
                room_token = next(
                    (
                        token
                        for token in reversed(tokens)
                        if re.search(
                            r"(?:楼|馆|教室|实验室|实验中心|体育场|建筑|信息|\d+号)[^\s，,]*$",
                            token,
                        )
                    ),
                    "",
                )
                if room_token:
                    location = room_token
                    break
        return {
            "id": identifier,
            "course_name": course_name,
            "course_code": course_code,
            "teaching_class_id": teaching_class_id,
            "weekday": cls._integer(row.get("dayOfWeek") or row.get("SKXQ")),
            "start_section": cls._integer(row.get("beginSection") or row.get("KSJC")),
            "end_section": cls._integer(row.get("endSection") or row.get("JSJC")),
            "start_time": cls._text(row.get("beginTime") or row.get("KSSJ")),
            "end_time": cls._text(row.get("endTime") or row.get("JSSJ")),
            "teachers": cls._course_teachers(row),
            "classes": cls._list_or_text(
                row.get("classes") or row.get("className") or row.get("classNames")
            ),
            "location": location,
            "campus": campus,
            "course_nature": course_nature,
            "assessment_type": assessment_type,
            "grading_scheme": grading_scheme,
            "week_text": (
                row.get("weeks")
                or row.get("weekList")
                or row.get("week_list")
                or row.get("weekText")
                or ""
            ),
            "weeks_and_teachers": row.get("weeksAndTeachers") or "",
            "cell_details": cell_details,
            "title_details": title_details,
            "tags": tags,
            "preselected": preselected,
            "color": cls._safe_color(row.get("color")),
        }

    @classmethod
    def _list_or_text(cls, value: Any) -> List[str]:
        if isinstance(value, list):
            result = []
            for item in value:
                text = cls._text(
                    item.get("name") if isinstance(item, dict) else item
                )
                if text:
                    result.append(text)
            return result
        text = cls._text(value)
        return [part.strip() for part in re.split(r"[,，、;/]", text) if part.strip()]

    @classmethod
    def _course_teachers(cls, row: Mapping[str, Any]) -> List[str]:
        direct = row.get("teachers") or row.get("teacherName")
        if direct:
            return cls._list_or_text(direct)

        value = row.get("weeksAndTeachers")
        if isinstance(value, list):
            result: List[str] = []
            for item in value:
                if isinstance(item, dict):
                    result.extend(
                        cls._list_or_text(
                            item.get("teacherName") or item.get("teachers") or item.get("name")
                        )
                    )
                else:
                    result.extend(cls._list_or_text(item))
            return list(dict.fromkeys(result))

        parts = [part.strip() for part in cls._text(value).split("/") if part.strip()]
        teacher_parts = [
            re.sub(r"\[[^\]]+\]", "", part).strip()
            for part in parts
            if not re.search(r"(?:周|week)", part, flags=re.IGNORECASE)
        ]
        if not teacher_parts and parts:
            teacher_parts = [re.sub(r"\[[^\]]+\]", "", parts[-1]).strip()]
        result: List[str] = []
        for part in teacher_parts:
            result.extend(cls._list_or_text(part))
        return list(dict.fromkeys(result))

    @classmethod
    def _safe_color(cls, value: Any) -> str:
        color = cls._text(value)
        return color if re.fullmatch(r"#[0-9a-fA-F]{6}", color) else "#2563eb"

    @classmethod
    def _schedule_lists(cls, body: Any) -> Dict[str, List[Dict[str, Any]]]:
        if not isinstance(body, dict):
            raise TimetableError("课表响应结构异常")
        data: Any = body.get("datas", body)
        if isinstance(data, dict) and not any(
            key in data for key in ("arrangedList", "notArrangeList", "practiceList")
        ):
            nested = next(
                (
                    value
                    for value in data.values()
                    if isinstance(value, dict)
                    and any(
                        key in value
                        for key in ("arrangedList", "notArrangeList", "practiceList")
                    )
                ),
                None,
            )
            if nested is not None:
                data = nested
        if not isinstance(data, dict):
            raise TimetableError("课表响应结构异常")
        result: Dict[str, List[Dict[str, Any]]] = {}
        for key in ("arrangedList", "notArrangeList", "practiceList"):
            value = data.get(key, [])
            if not isinstance(value, list):
                raise TimetableError("课表响应结构异常")
            result[key] = [item for item in value if isinstance(item, dict)]
        return result

    def get_schedule(
        self,
        *,
        mode: str,
        term_code: str,
        campus_code: str,
        target_id: str = "",
        week: Optional[int] = None,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "XNXQDM": term_code,
            "XQDM": "" if campus_code == ALL_CAMPUSES_CODE else campus_code,
        }
        if week is not None:
            params["ZC"] = week
        if mode == "personal":
            url = self.PERSONAL_SCHEDULE_URL
        else:
            config = self._mode_config(mode)
            params.update({"CODE": target_id, "KBLX": config["schedule_type"]})
            url = self.TARGET_SCHEDULE_URL
        body = self._post(url, params, "获取课表")
        lists = self._schedule_lists(body)
        return {
            "courses": [
                self._course_from_row(row, index)
                for index, row in enumerate(lists["arrangedList"])
            ],
            "unscheduled": [
                {
                    "course_name": self._text(row.get("courseName") or row.get("KCM") or row.get("name"))
                    or (self._detail_texts(row.get("titleDetail")) or ["未命名课程"])[0],
                    "course_code": self._text(row.get("courseCode") or row.get("KCH")),
                    "details": self._detail_texts(row.get("titleDetail")),
                }
                for row in lists["notArrangeList"]
            ],
            "practices": [
                {
                    "course_name": self._text(row.get("courseName") or row.get("KCM") or row.get("name"))
                    or (self._detail_texts(row.get("titleDetail")) or ["未命名实践"])[0],
                    "course_code": self._text(row.get("courseCode") or row.get("KCH")),
                    "details": self._detail_texts(row.get("titleDetail")),
                }
                for row in lists["practiceList"]
            ],
        }
