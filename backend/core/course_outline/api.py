"""Official ``kccx`` course-outline read adapter."""

from __future__ import annotations

import json
import re
from typing import Any, Mapping

from .parser import (
    display_value,
    extract_dictionary_options,
    extract_container,
    extract_rows,
    normalize_course_code,
    present_rows,
)


class CourseOutlineError(RuntimeError):
    pass


class CourseOutlineAPI:
    BASE = "https://jwxt.neu.edu.cn/jwapp/sys/kccx"
    LIST_ACTION = "cxlb"
    FILTER_FIELDS = ("KCH", "KCM", "KKDWDM", "KCCCDM", "KCJBDM", "XF", "XS")
    DICTIONARY_ENDPOINTS = {
        "KKDWDM": "https://jwxt.neu.edu.cn/jwapp/code/8afd75f4-fb19-4120-9d49-2e6e1de99f8f.do",
        "KCCCDM": "https://jwxt.neu.edu.cn/jwapp/code/ebb0e845-6ae0-44ab-aa5d-4b9f57ed1a1d.do",
        "KCJBDM": "https://jwxt.neu.edu.cn/jwapp/code/41507e52-6b2f-4e5e-b5de-b65bba206dcf.do",
    }
    SECTION_ENDPOINTS = {
        "teaching": (
            ("outline", "cxkcdgxx.do"),
            ("objectives", "cxkcmbxx.do"),
            ("objective_support", "kcmbybyzccx.do"),
            ("objective_content", "cxkcmbhnrdgx.do"),
            ("attainment", "cxkcmbdcbz.do"),
        ),
        "assessment": (
            ("score_formula", "kccjlrgs.do"),
            ("grading_method", "cxkccjpdff.do"),
            ("assessment_types", "cxkhxs.do"),
            ("assessment_links", "cxkhhjsz.do"),
            ("assessment_weights", "cxkhxscjzb.do"),
        ),
        "governance": (
            ("quality_improvement", "cxkczlpjhgjjz.do"),
            ("authors", "cxzbrxgxx.do"),
            ("attachments", "cxkcdgfj.do"),
        ),
    }
    HEADERS = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Referer": f"{BASE}/*default/index.do#/dgcx",
        "X-Requested-With": "XMLHttpRequest",
    }

    def __init__(self, auth_client):
        self.auth = auth_client

    def _post(self, path: str, data: Mapping[str, Any] | None = None) -> dict[str, Any]:
        response = self.auth.post(
            f"{self.BASE}/{path.lstrip('/')}", data=dict(data or {}), headers=self.HEADERS
        )
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError as exc:
            raise CourseOutlineError("课程大纲服务返回了无法解析的数据") from exc
        if not isinstance(payload, dict):
            raise CourseOutlineError("课程大纲服务响应格式异常")
        return payload

    def _get_json(self, url: str) -> Any:
        response = self.auth.get(url, headers={
            "Accept": self.HEADERS["Accept"], "Referer": self.HEADERS["Referer"],
            "X-Requested-With": self.HEADERS["X-Requested-With"],
        })
        response.raise_for_status()
        try:
            return response.json()
        except ValueError as exc:
            raise CourseOutlineError("课程大纲筛选项返回了无法解析的数据") from exc

    @staticmethod
    def _rule(name: str, value: Any, builder: str = "include") -> dict[str, str]:
        return {"name": name, "builder": builder, "linkOpt": "AND", "value": str(value)}

    def search(self, *, keyword: str = "", filters: Mapping[str, Any] | None = None,
               page: int = 1, page_size: int = 20) -> dict[str, Any]:
        rules: list[dict[str, str]] = []
        keyword = str(keyword or "").strip()
        if keyword:
            rules.append(self._rule("KCH" if re.fullmatch(r"[A-Za-z0-9._-]+", keyword) else "KCM", keyword))
        for field, value in (filters or {}).items():
            if field not in self.FILTER_FIELDS or value in (None, "", []):
                continue
            builder = "between" if field in {"XF", "XS"} and isinstance(value, (list, tuple)) else "equal"
            if builder == "between" and not any(item not in (None, "") for item in value):
                continue
            normalized = ",".join(str(item) for item in value) if builder == "between" else value
            rules.append(self._rule(field, normalized, builder))
        payload = self._post("modules/dgcx/cxlb.do", {
            "querySetting": json.dumps(rules, ensure_ascii=False),
            "pageNumber": page,
            "pageSize": page_size,
            "*order": "+KCH",
        })
        rows = extract_rows(payload, self.LIST_ACTION)
        container = extract_container(payload, self.LIST_ACTION)
        items = [self._normalize_list_row(row) for row in rows]
        return {
            "items": [item for item in items if item["course_code"]],
            "total": int(container.get("totalSize") or len(items)),
            "page": int(container.get("pageNumber") or page),
            "page_size": int(container.get("pageSize") or page_size),
        }

    def search_schema(self) -> dict[str, Any]:
        options = {field: [] for field in ("KKDWDM", "KCCCDM", "KCJBDM")}
        errors: list[str] = []
        for field, url in self.DICTIONARY_ENDPOINTS.items():
            try:
                options[field] = extract_dictionary_options(self._get_json(url))
            except Exception as exc:
                errors.append(f"{field}:{type(exc).__name__}")
        return {
            "fields": [
                {"key": "KKDWDM", "label": "开课单位", "type": "select", "enabled": bool(options["KKDWDM"]), "options": options["KKDWDM"]},
                {"key": "KCCCDM", "label": "课程层次", "type": "select", "enabled": bool(options["KCCCDM"]), "options": options["KCCCDM"]},
                {"key": "KCJBDM", "label": "课程级别", "type": "select", "enabled": bool(options["KCJBDM"]), "options": options["KCJBDM"]},
                {"key": "XF", "label": "学分", "type": "range-preset", "enabled": True},
                {"key": "XS", "label": "学时", "type": "range-preset", "enabled": True},
            ],
            "partial": bool(errors), "failed_fields": errors,
        }

    def overview(self, course_code: str) -> dict[str, Any]:
        code = normalize_course_code(course_code)
        self._post("api/kcdgwhgl/cshdgsj.do", {"KCH": code})
        sections: dict[str, Any] = {}
        failures: dict[str, str] = {}
        for key, endpoint in (
            ("basic", "cxkcxxx.do"),
            ("outline", "cxkcdgxx.do"),
            ("introduction", "cxkcjcxx.do"),
        ):
            try:
                payload = self._post(f"modules/kcdgwhgl/{endpoint}", {"KCH": code})
                sections[key] = extract_rows(payload, endpoint.removesuffix(".do"))
            except Exception as exc:
                failures[key] = type(exc).__name__
        basic = (sections.get("basic") or [{}])[0]
        outline = (sections.get("outline") or [{}])[0]
        introduction = (sections.get("introduction") or [{}])[0]
        return {
            "course_code": code,
            "course_name": display_value(basic, "KCM"),
            "department": display_value(basic, "KKDWDM"),
            "credits": basic.get("XF"),
            "hours": basic.get("XS"),
            "assessment_method_code": str(basic.get("KSLXDM") or ""),
            "assessment_method": display_value(basic, "KSLXDM"),
            "grading_scale_code": str(basic.get("CJJLFS") or ""),
            "grading_scale": display_value(basic, "CJJLFS"),
            "version": str(basic.get("BBWID") or basic.get("WID") or ""),
            "introduction": introduction.get("KCJJ") or introduction.get("KCJJYW") or "",
            "course_nature": display_value(outline, "KCXZDM"),
            "applicable_majors": outline.get("SYZY") or "",
            "prerequisites": outline.get("XXKC") or "",
            "textbooks": outline.get("SYJC") or outline.get("JCXX") or "",
            "failures": failures,
        }

    def sections(self, course_code: str, group: str) -> dict[str, Any]:
        code = normalize_course_code(course_code)
        if group not in self.SECTION_ENDPOINTS:
            raise ValueError("invalid section group")
        raw: dict[str, list[dict[str, Any]]] = {}
        failures: dict[str, str] = {}
        for key, endpoint in self.SECTION_ENDPOINTS[group]:
            try:
                payload = self._post(f"modules/kcdgwhgl/{endpoint}", {"KCH": code})
                raw[key] = extract_rows(payload, endpoint.removesuffix(".do"))
            except Exception as exc:
                failures[key] = type(exc).__name__
        result: list[dict[str, Any]] = []
        if group == "assessment":
            result.extend(self._present_assessment(raw))
        else:
            for key, _endpoint in self.SECTION_ENDPOINTS[group]:
                presented = present_rows(key, raw.get(key, []))
                if presented.get("items") or presented.get("rows"):
                    result.append(presented)
        return {"course_code": code, "group": group, "sections": result, "failures": failures}

    @staticmethod
    def _present_assessment(raw: Mapping[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        grading = present_rows("grading_method", raw.get("grading_method", []))
        if grading.get("items"):
            result.append(grading)
        types = {str(row.get("WID") or ""): display_value(row, "KHXSMC") for row in raw.get("assessment_types", [])}
        links = {str(row.get("WID") or ""): display_value(row, "KCMB") for row in raw.get("assessment_links", [])}
        matrix_rows = []
        for row in sorted(raw.get("assessment_weights", []), key=lambda item: str(item.get("PX") or "")):
            target = links.get(str(row.get("KHHJWID") or ""), "")
            method = types.get(str(row.get("KHXSWID") or ""), "")
            weight = row.get("CJZB")
            if any(value not in (None, "") for value in (target, method, weight)):
                matrix_rows.append({"课程目标": target or "—", "考核形式": method or "—", "成绩占比": weight if weight not in (None, "") else "—"})
        if matrix_rows:
            result.append({"kind": "table", "title": "考核矩阵", "columns": ["课程目标", "考核形式", "成绩占比"], "rows": matrix_rows})
        return result

    @staticmethod
    def _normalize_list_row(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "course_code": str(row.get("KCH") or "").strip(),
            "course_name": display_value(row, "KCM"),
            "department": display_value(row, "KKDWDM"),
            "level": display_value(row, "KCCCDM"),
            "grade": display_value(row, "KCJBDM"),
            "credits": row.get("XF"),
            "hours": row.get("XS"),
        }
