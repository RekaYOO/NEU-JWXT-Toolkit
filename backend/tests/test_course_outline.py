import json

import pytest
from fastapi import Response

from backend.app.routers import course_outline as router
from backend.app.schemas.course_outline import (
    CourseOutlineMetadataReadRequest,
    CourseOutlineSearchRequest,
)
from backend.core.course_outline import CourseOutlineAPI, extract_rows


class FakeResponse:
    status_code = 200
    headers = {}
    content = b""
    url = "https://jwxt.neu.edu.cn/"

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload

    def raise_for_status(self):
        return None


class FakeClient:
    def __init__(self, *payloads):
        self.payloads = list(payloads)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return FakeResponse(self.payloads.pop(0))

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return FakeResponse(self.payloads.pop(0))


def test_extract_rows_accepts_top_level_and_nested_action_containers():
    assert extract_rows({"rows": [{"KCH": "A"}]}) == [{"KCH": "A"}]
    assert extract_rows({"datas": {"cxlb": {"rows": [{"KCH": "B"}]}}}, "cxlb") == [{"KCH": "B"}]
    assert extract_rows({"datas": {"other": {"rows": {"KCH": "C"}}}}) == [{"KCH": "C"}]


def test_search_builds_server_side_pagination_and_seven_field_query():
    client = FakeClient({
        "datas": {"cxlb": {"rows": [{
            "KCH": "IE1001", "KCM": "工业工程", "KKDWDM_DISPLAY": "管理学院",
            "XF": "2", "XS": "32",
        }], "totalSize": 49, "pageNumber": 2, "pageSize": 20}}
    })
    result = CourseOutlineAPI(client).search(
        keyword="工业", filters={"KKDWDM": "12", "XF": [1, 3]}, page=2, page_size=20
    )
    rules = json.loads(client.calls[0][1]["data"]["querySetting"])

    assert result["total"] == 49
    assert result["items"][0]["department"] == "管理学院"
    assert client.calls[0][1]["data"]["*order"] == "+KCH"
    assert {rule["name"] for rule in rules} == {"KCM", "KKDWDM", "XF"}


def test_overview_extracts_only_normalized_metadata_and_keeps_partial_failures():
    client = FakeClient(
        {"rows": [{"WID": "init"}]},
        {"datas": {"cxkcxxx": {"rows": [{
            "KCH": "A100", "KCM": "测试课程", "KSLXDM": "01",
            "KSLXDM_DISPLAY": "考试", "CJJLFS": "01",
            "CJJLFS_DISPLAY": "百分制", "BBWID": "v2",
        }]}}},
        {"rows": [{"SYJC": "教材"}]},
        {"rows": [{"KCJJ": "简介"}]},
    )
    overview = CourseOutlineAPI(client).overview("A100")

    assert overview["assessment_method"] == "考试"
    assert overview["grading_scale"] == "百分制"
    assert overview["version"] == "v2"
    assert overview["introduction"] == "简介"


def test_search_schema_reads_the_three_official_code_endpoints():
    client = FakeClient(
        [{"id": "01", "name": "理学院"}],
        {"rows": [{"DM": "1", "MC": "本科"}]},
        {"datas": {"items": [{"value": "A", "label": "国家级"}]}},
    )
    schema = CourseOutlineAPI(client).search_schema()

    assert [item["enabled"] for item in schema["fields"][:3]] == [True, True, True]
    assert schema["fields"][0]["options"] == [{"value": "01", "label": "理学院"}]
    assert [call[0] for call in client.calls] == list(CourseOutlineAPI.DICTIONARY_ENDPOINTS.values())


def test_teaching_sections_are_semantic_and_drop_internal_identifiers():
    payloads = [
        {"rows": [{"WID": "secret", "SYZY": "工业工程", "KCXZDM_DISPLAY": "必修"}]},
        {"rows": [{"WID": "secret", "KCMB": "掌握基础知识"}]},
        {"rows": [{"WID": "secret", "KCMB": "目标 1", "BYYQ": "指标点 2", "CD_DISPLAY": "H"}]},
        {"rows": [{"WID": "secret", "ZJ": "第一章", "JXNR": "绪论", "KTJSXS": "2"}]},
        {"rows": [{"WID": "secret", "DCQKPJDJ_DISPLAY": "优秀", "KCMBDCQKPJBZ": "达到目标", "PJDJFS": ">=90"}]},
    ]
    result = CourseOutlineAPI(FakeClient(*payloads)).sections("A100", "teaching")
    serialized = json.dumps(result, ensure_ascii=False)

    assert "WID" not in serialized and "secret" not in serialized
    assert "适用专业" in serialized and "课程目标达成标准" in serialized and "达到目标" in serialized


def test_assessment_sections_join_relation_ids_into_a_readable_matrix():
    client = FakeClient(
        {"rows": []},
        {"rows": [{"KCCJPDFF": "平时成绩与期末考试综合评定"}]},
        {"rows": [{"WID": "type-1", "KHXSMC": "期末考试"}]},
        {"rows": [{"WID": "target-1", "KCMB": "目标 1"}]},
        {"rows": [{"KHXSWID": "type-1", "KHHJWID": "target-1", "CJZB": "60%"}]},
    )
    result = CourseOutlineAPI(client).sections("A100", "assessment")
    serialized = json.dumps(result, ensure_ascii=False)

    assert "期末考试" in serialized and "目标 1" in serialized and "60%" in serialized
    assert "type-1" not in serialized and "target-1" not in serialized and "KHXSWID" not in serialized


def test_search_request_rejects_unknown_filters():
    with pytest.raises(ValueError):
        CourseOutlineSearchRequest(filters={"COOKIE": "secret"})


def test_metadata_read_request_normalizes_and_deduplicates_codes():
    request = CourseOutlineMetadataReadRequest(
        course_codes=[" A100 ", "A100", "B-200"],
    )
    assert request.course_codes == ["A100", "B-200"]


def test_metadata_read_reuses_cached_plan_items_and_ignores_missing(monkeypatch):
    from types import SimpleNamespace

    payload = {
        "course_code": "A100",
        "assessment_method": "考试",
        "grading_scale": "百分制",
        "status": "success",
    }

    class Store:
        def get(self, key):
            return SimpleNamespace(payload=payload) if key.variant == "course:A100" else None

    monkeypatch.setattr(
        router,
        "get_cache_coordinator",
        lambda: SimpleNamespace(store=Store()),
    )
    result = router.read_metadata(
        CourseOutlineMetadataReadRequest(course_codes=["A100", "B200"]),
        SimpleNamespace(username="student"),
    )

    assert result == {"items": [payload]}


def test_search_and_detail_routes_mark_responses_no_store(monkeypatch):
    monkeypatch.setattr(CourseOutlineAPI, "search", lambda self, **kwargs: {"items": [], "total": 0, "page": 1, "page_size": 20})
    monkeypatch.setattr(CourseOutlineAPI, "overview", lambda self, code: {"course_code": code})
    response = Response()
    router.search(CourseOutlineSearchRequest(), response, object())
    assert response.headers["cache-control"] == "no-store"


def test_metadata_fetch_has_strict_storage_whitelist(monkeypatch):
    from backend.app import dependencies
    from backend.core.cache import CacheKey, FetchContext

    monkeypatch.setattr(dependencies, "_cache_client", lambda _context: object())
    monkeypatch.setattr(dependencies.CourseOutlineAPI, "overview", lambda self, code: {
        "course_name": "课程", "assessment_method": "考试", "assessment_method_code": "01",
        "grading_scale": "百分制", "grading_scale_code": "01", "version": "v1",
        "introduction": "禁止落库的正文", "textbooks": [{"name": "禁止落库的教材"}],
    })
    payload = dependencies._fetch_course_outline_metadata_resource(FetchContext(
        CacheKey("student", "course-outline-metadata", "course:A100"), 1,
        "metadata_sync:fingerprint",
    ))

    assert set(payload) == {
        "course_code", "course_name", "assessment_method_code", "assessment_method",
        "grading_scale_code", "grading_scale", "outline_version", "plan_fingerprint", "status",
    }
    assert "正文" not in json.dumps(payload, ensure_ascii=False)
