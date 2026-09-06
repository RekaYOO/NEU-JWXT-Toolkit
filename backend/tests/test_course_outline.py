import json
from types import SimpleNamespace

import pytest
from fastapi import Response

from backend.app.routers import course_outline as router
from backend.app.schemas.course_outline import (
    CourseOutlineMetadataReadRequest,
    CourseOutlineSearchRequest,
)
from backend.core.course_outline import (
    CourseOutlineAPI,
    CourseOutlineMetadataSyncService,
    extract_rows,
)


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

    def read_many(*, account_id, resources):
        assert account_id == "student"
        return {
            key: (SimpleNamespace(payload=payload), False)
            if key[1] == "course:A100" else (None, True)
            for key in resources
        }

    monkeypatch.setattr(
        router,
        "get_cache_coordinator",
        lambda: SimpleNamespace(read_many=read_many),
    )
    result = router.read_metadata(
        CourseOutlineMetadataReadRequest(course_codes=["A100", "B200"]),
        SimpleNamespace(username="student"),
    )

    assert result == {"items": [{**payload, "needs_sync": False}]}


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
    monkeypatch.setattr(dependencies.CourseOutlineAPI, "metadata", lambda self, code: {
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


def test_metadata_sync_course_failure_does_not_leave_batch_running():
    class Store:
        def get(self, _key):
            raise RuntimeError("cache unavailable")

    service = CourseOutlineMetadataSyncService(
        cache_store=Store(),
        cache_coordinator=SimpleNamespace(read=lambda **_kwargs: Store().get(None)),
        auth_epoch=lambda: 1,
    )
    service._state.account = "student"
    service._state.running = True

    service._run("student", [{"course_code": "A100"}], False)

    state = service.status("student")
    assert state["running"] is False
    assert state["current_course"] == ""
    assert state["failed"] == 1
    assert state["errors"] == ["A100"]


def test_metadata_only_reads_initialization_and_basic_information():
    client = FakeClient(
        {"rows": []},
        {"datas": {"cxkcxxx": {"rows": [{
            "KCH": "A100", "KCM": "测试课程",
            "KSLXDM_DISPLAY": "考试", "CJJLFS_DISPLAY": "百分制",
            "KCJJ": "正文不得读取或存储",
        }]}}},
    )
    result = CourseOutlineAPI(client).metadata("A100")
    assert result["assessment_method"] == "考试"
    assert result["grading_scale"] == "百分制"
    assert "KCJJ" not in result
    assert len(client.calls) == 2
    assert client.calls[-1][0].endswith("/cxkcxxx.do")


@pytest.mark.parametrize("payload", [
    {"success": False, "msg": "unavailable"},
    {"datas": {"cxkcxxx": {"error": "unavailable"}}},
    {"rows": None},
])
def test_metadata_basic_errors_are_not_negative_cache_hits(payload):
    from backend.core.course_outline.api import CourseOutlineError

    with pytest.raises(CourseOutlineError):
        CourseOutlineAPI(FakeClient({"rows": []}, payload)).metadata("A100")


def test_metadata_empty_basic_rows_are_legitimate_missing_outlines():
    result = CourseOutlineAPI(FakeClient({"rows": []}, {"rows": []})).metadata("A100")
    assert result["course_code"] == "A100"
    assert result["course_name"] == ""
    assert result["grading_scale"] == ""


def test_metadata_sync_keeps_all_failed_codes_for_retry():
    from unittest.mock import Mock

    store = Mock()
    store.get.side_effect = RuntimeError("cache unavailable")
    service = CourseOutlineMetadataSyncService(
        cache_store=store, cache_coordinator=SimpleNamespace(read=store.get), auth_epoch=lambda: 1,
    )
    service._state.account = "student"
    courses = [{"course_code": f"A{index:03}"} for index in range(15)]
    service._run("student", courses, False)
    assert service.status("student")["errors"] == [course["course_code"] for course in courses]


def test_metadata_sync_identity_change_stops_waiting_and_remaining_courses(monkeypatch):
    from unittest.mock import Mock
    from backend.core.cache.models import JobStatus

    epoch = [1]
    coordinator = Mock()
    coordinator.read.return_value = (None, True)
    coordinator.submit.return_value = SimpleNamespace(job_id="job")
    coordinator.get_job.return_value = SimpleNamespace(status=JobStatus.RUNNING)
    service = CourseOutlineMetadataSyncService(
        cache_store=SimpleNamespace(get=lambda _key: None),
        cache_coordinator=coordinator, auth_epoch=lambda: epoch[0],
    )
    service._state.account = "student"
    monkeypatch.setattr("backend.core.course_outline.service.time.sleep", lambda _seconds: epoch.__setitem__(0, 2))
    service._run("student", [{"course_code": "A100"}, {"course_code": "B200"}], False)
    assert coordinator.submit.call_count == 1
    assert coordinator.submit.call_args.kwargs["identity_epoch"] == 1
    assert service.status("student")["cancelled"] is True
    assert service.status("student")["running"] is False


def test_metadata_old_worker_cannot_finish_new_identity_batch():
    from backend.core.course_outline.service import SyncState

    service = CourseOutlineMetadataSyncService(
        cache_store=SimpleNamespace(), cache_coordinator=SimpleNamespace(), auth_epoch=lambda: 2,
    )
    old = SyncState(account="old", running=True)
    service._state = SyncState(account="new", running=True)
    service._run("old", [{"course_code": "A100"}], False, old, 1)
    assert old.running is False
    assert old.cancelled is True
    assert service.status("new")["running"] is True
    assert service.cancel("old")["account"] == "old"
    assert service.cancel("old")["errors"] == []
    assert service.status("new")["cancelled"] is False


def test_metadata_sync_distinguishes_reused_batch_and_new_identity(monkeypatch):
    from unittest.mock import Mock

    threads = []

    def make_thread(**kwargs):
        thread = Mock()
        thread.is_alive.return_value = True
        threads.append((thread, kwargs))
        return thread

    monkeypatch.setattr("backend.core.course_outline.service.threading.Thread", make_thread)
    epoch = [1]
    service = CourseOutlineMetadataSyncService(
        cache_store=SimpleNamespace(), cache_coordinator=SimpleNamespace(), auth_epoch=lambda: epoch[0],
    )
    assert service.start("first", [{"course_code": "A100"}])["accepted"] is True
    assert service.start("first", [{"course_code": "B200"}])["accepted"] is False
    assert len(threads) == 1
    old = service._state
    epoch[0] = 2
    next_state = service.start("second", [{"course_code": "B200"}])
    assert next_state["accepted"] is True
    assert next_state["account"] == "second"
    assert old.cancelled is True
    assert len(threads) == 2


def test_metadata_sync_failed_thread_start_does_not_leave_running(monkeypatch):
    from unittest.mock import Mock

    thread = Mock()
    thread.start.side_effect = RuntimeError("thread unavailable")
    monkeypatch.setattr("backend.core.course_outline.service.threading.Thread", lambda **_kwargs: thread)
    service = CourseOutlineMetadataSyncService(
        cache_store=SimpleNamespace(), cache_coordinator=SimpleNamespace(), auth_epoch=lambda: 1,
    )
    with pytest.raises(RuntimeError):
        service.start("student", [{"course_code": "A100"}])
    assert service.status("student")["running"] is False


@pytest.mark.parametrize("status,days,stale,expected", [
    ("success", 1, False, False),
    ("success", 31, True, True),
    ("not_found", 6, False, False),
    ("not_found", 8, False, True),
    ("failed", 1, False, True),
    ("success", 1, True, True),
])
def test_metadata_refresh_policy_uses_checked_time(status, days, stale, expected):
    from datetime import timedelta
    from backend.core.cache.models import utc_now
    from backend.core.course_outline.service import metadata_needs_sync

    entry = SimpleNamespace(
        payload={"status": status}, last_checked_at=utc_now() - timedelta(days=days),
        saved_at=utc_now() - timedelta(days=100),
    )
    assert metadata_needs_sync(entry, stale) is expected


def test_metadata_persists_reopens_and_retains_previous_values_on_failure(tmp_path, monkeypatch):
    from backend.app import dependencies
    from backend.core.cache import CacheCoordinator, CacheKey, CacheStore

    store = CacheStore(tmp_path / "cache.db")
    coordinator = CacheCoordinator(store, dependencies._cache_registry)
    service = CourseOutlineMetadataSyncService(
        cache_store=store, cache_coordinator=coordinator, auth_epoch=lambda: 1,
    )
    monkeypatch.setattr(dependencies, "_cache_client", lambda _context: FakeClient(
        {"rows": []},
        {"rows": [{
            "KCH": "A100", "KCM": "测试课程",
            "KSLXDM_DISPLAY": "考试", "CJJLFS_DISPLAY": "百分制",
        }]},
    ))
    courses = [{"course_code": "A100"}]
    try:
        service.start("student", courses)
        service._thread.join(3)
        assert service.status("student")["running"] is False
        assert service.status("student")["completed"] == 1
        key = CacheKey("student", "course-outline-metadata", "course:A100")
        reopened = CacheStore(tmp_path / "cache.db")
        entry = reopened.get(key)
        assert entry.schema_version == 2
        assert entry.payload["assessment_method"] == "考试"
        assert entry.payload["grading_scale"] == "百分制"
        assert reopened.get(CacheKey("other", key.resource, key.variant)) is None

        def unavailable(_context):
            raise RuntimeError("school unavailable")

        monkeypatch.setattr(dependencies, "_cache_client", unavailable)
        # A fresh successful entry is reused without contacting the school.
        service.start("student", courses)
        service._thread.join(3)
        assert service.status("student")["completed"] == 1
        assert service.status("student")["failed"] == 0
        # Explicit refresh failure must not overwrite the saved values.
        service.start("student", courses, force=True)
        service._thread.join(3)
        assert service.status("student")["running"] is False
        assert service.status("student")["failed"] == 1
        assert reopened.get(key).payload == entry.payload
    finally:
        service.cancel("student")
        if service._thread:
            service._thread.join(3)
        coordinator.shutdown(timeout=3)


def test_metadata_read_marks_old_schema_for_refresh_without_discarding_values(tmp_path, monkeypatch):
    from backend.app import dependencies
    from backend.core.cache import CacheCoordinator, CacheKey, CacheStore, PayloadType

    store = CacheStore(tmp_path / "cache.db")
    store.commit_success(
        key=CacheKey("student", "course-outline-metadata", "course:A100"),
        schema_version=1, revision_algorithm_version=1, payload_type=PayloadType.JSON,
        payload={"course_code": "A100", "status": "not_found"}, revision="v1:old",
        dependency_revisions={}, changes={}, reason="test",
    )
    coordinator = CacheCoordinator(store, dependencies._cache_registry, autostart=False)
    monkeypatch.setattr(router, "get_cache_coordinator", lambda: coordinator)
    result = router.read_metadata(
        CourseOutlineMetadataReadRequest(course_codes=["A100"]), SimpleNamespace(username="student"),
    )
    assert result["items"] == [{"course_code": "A100", "status": "not_found", "needs_sync": True}]
