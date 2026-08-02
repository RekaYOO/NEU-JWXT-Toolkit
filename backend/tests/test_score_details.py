from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.app.schemas.scores import CourseScoreDetailResponse, CourseScoreModel
from backend.app.schemas.scores import ScoreDetailQueryRequest
from backend.core.cache import CacheKey, CacheStore, PayloadType, RefreshStatus
from backend.core.cache.resources import score_detail_variant
from backend.core.academic import api as academic_module
from backend.core.academic.api import (
    AcademicAPI,
    AcademicDataError,
    ScoreDetailCircuitOpen,
)


class FakeResponse:
    def __init__(self, payload=None, *, status_code=200, error=None, headers=None):
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {}
        self._error = error

    def json(self):
        if self._error:
            raise self._error
        return self._payload


class FakeClient:
    def __init__(self, *, gets=(), posts=()):
        self.gets = list(gets)
        self.posts = list(posts)
        self.post_calls = []

    def get(self, *_args, **_kwargs):
        return self.gets.pop(0)

    def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        return self.posts.pop(0)


@pytest.fixture(autouse=True)
def bypass_score_detail_gate(monkeypatch):
    monkeypatch.setattr(academic_module._DETAIL_GATE, "before_request", lambda: None)
    monkeypatch.setattr(academic_module._DETAIL_GATE, "success", lambda: None)
    monkeypatch.setattr(academic_module._DETAIL_GATE, "failure", lambda *_args: None)


def test_score_detail_preserves_dynamic_items_and_empty_fields():
    payload = {
        "code": "0",
        "datas": {
            "details": {
                "score": "79",
                "gradePoint": "2.9",
                "pass": True,
                "itemScores": [
                    {
                        "code": "CUSTOM",
                        "name": "课堂表现",
                        "value": "92",
                        "pass": True,
                        "highestScoreInProportion": False,
                    },
                    {
                        "code": "CUSTOM",
                        "name": "",
                        "value": None,
                        "pass": None,
                        "highestScoreInProportion": True,
                    },
                ],
            }
        },
    }
    client = FakeClient(posts=[FakeResponse(payload)])

    detail = AcademicAPI(client).get_score_detail("opaque-detail-ref")

    assert detail.score == "79"
    assert detail.grade_point == "2.9"
    assert detail.passed is True
    assert [item.code for item in detail.items] == ["CUSTOM", "CUSTOM"]
    assert detail.items[0].name == "课堂表现"
    assert detail.items[1].name == ""
    assert detail.items[1].value is None
    assert detail.items[1].passed is None
    assert detail.items[1].highest_score_in_proportion is True
    assert client.post_calls[0][1]["data"] == {"WID": "opaque-detail-ref"}


@pytest.mark.parametrize("raw_items", [None, []])
def test_score_detail_accepts_missing_or_empty_items(raw_items):
    details = {"score": "85", "gradePoint": "3.5", "pass": True}
    if raw_items is not None:
        details["itemScores"] = raw_items
    client = FakeClient(posts=[FakeResponse({
        "code": "0", "datas": {"details": details}
    })])

    result = AcademicAPI(client).get_score_detail("detail-ref")

    assert result.items == []
    assert result.score == "85"


@pytest.mark.parametrize(
    "response",
    [
        FakeResponse({"code": "1", "datas": {}}),
        FakeResponse({"code": "0", "datas": {"details": None}}),
        FakeResponse(error=ValueError("not json")),
    ],
)
def test_score_detail_rejects_untrustworthy_responses(response):
    with pytest.raises(AcademicDataError):
        AcademicAPI(FakeClient(posts=[response])).get_score_detail("detail-ref")


@pytest.mark.parametrize("status_code", [403, 429, 500, 503])
def test_score_detail_rejects_remote_refusal_and_server_errors(status_code):
    with pytest.raises(AcademicDataError):
        AcademicAPI(FakeClient(posts=[FakeResponse({}, status_code=status_code)])).get_score_detail(
            "detail-ref"
        )


def test_public_score_models_never_expose_detail_reference():
    course = CourseScoreModel(
        name="course",
        code="CODE",
        score="90",
        score_value=90,
        gpa=4,
        credit=2,
        term="2025-2026-2",
        term_display="term",
        course_type="required",
        course_category="category",
        exam_type="exam",
        is_passed=True,
    )
    detail = CourseScoreDetailResponse(
        course_code="CODE",
        term="2025-2026-2",
        item_scores=[{"code": "FINAL", "name": "final", "value": "90"}],
    )

    public_payload = {
        "course": course.model_dump(by_alias=True),
        "detail": detail.model_dump(by_alias=True),
    }
    serialized = str(public_payload).lower()
    assert "detail_ref" not in serialized
    assert "wid" not in serialized


def test_score_detail_gate_spaces_request_starts_with_jitter(monkeypatch):
    clock = {"now": 100.0}
    sleeps = []

    def fake_sleep(seconds):
        sleeps.append(seconds)
        clock["now"] += seconds

    monkeypatch.setattr(academic_module.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(academic_module.time, "sleep", fake_sleep)
    monkeypatch.setattr(academic_module.random, "uniform", lambda _a, _b: 0.2)
    gate = academic_module._ScoreDetailGate()

    gate.before_request()
    clock["now"] += 0.1
    gate.before_request()

    assert sleeps == [pytest.approx(1.3)]
    assert clock["now"] == pytest.approx(101.4)


def test_suspicious_html_opens_score_detail_circuit(monkeypatch):
    clock = {"now": 100.0}
    gate = academic_module._ScoreDetailGate()
    monkeypatch.setattr(academic_module, "_DETAIL_GATE", gate)
    monkeypatch.setattr(academic_module.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(academic_module.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(academic_module.random, "uniform", lambda _a, _b: 0.0)
    client = FakeClient(posts=[FakeResponse(error=ValueError("html"))])

    with pytest.raises(AcademicDataError):
        AcademicAPI(client).get_score_detail("detail-ref")
    with pytest.raises(ScoreDetailCircuitOpen):
        AcademicAPI(client).get_score_detail("detail-ref")


def test_retry_after_extends_score_detail_circuit(monkeypatch):
    clock = {"now": 100.0}
    gate = academic_module._ScoreDetailGate()
    monkeypatch.setattr(academic_module, "_DETAIL_GATE", gate)
    monkeypatch.setattr(academic_module.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(academic_module.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(academic_module.random, "uniform", lambda _a, _b: 0.0)
    client = FakeClient(posts=[FakeResponse(
        {}, status_code=429, headers={"Retry-After": "120"}
    )])

    with pytest.raises(AcademicDataError):
        AcademicAPI(client).get_score_detail("detail-ref")
    clock["now"] += 61
    with pytest.raises(ScoreDetailCircuitOpen):
        gate.before_request()


def test_score_detail_circuit_escalates_to_one_five_thirty_minutes(monkeypatch):
    clock = {"now": 100.0}
    gate = academic_module._ScoreDetailGate()
    monkeypatch.setattr(academic_module.time, "monotonic", lambda: clock["now"])

    delays = []
    for _ in range(3):
        gate.failure()
        delays.append(gate._blocked_until - clock["now"])
        clock["now"] = gate._blocked_until

    assert delays == [60.0, 300.0, 1800.0]


def _store_cached_detail(store, *, account="account", detail_ref="private-wid"):
    score_key = CacheKey(account, "scores")
    store.commit_success(
        key=score_key,
        schema_version=2,
        revision_algorithm_version=1,
        payload_type=PayloadType.JSON,
        payload={
            "scores": [{
                "code": "CODE",
                "term": "2025-2026-2",
                "score": "90",
                "gpa": 4.0,
                "detail_ref": detail_ref,
            }],
            "overall_gpa": 4.0,
        },
        revision="v1:scores",
        dependency_revisions={},
        changes={},
        reason="manual",
    )
    detail_key = CacheKey(
        account,
        "score-details",
        score_detail_variant("CODE", "2025-2026-2"),
    )
    store.commit_success(
        key=detail_key,
        schema_version=1,
        revision_algorithm_version=1,
        payload_type=PayloadType.JSON,
        payload={
            "course_code": "CODE",
            "term": "2025-2026-2",
            "source_score": "90",
            "source_gpa": 4.0,
            "score": "90",
            "grade_point": "4.0",
            "pass": True,
            "item_scores": [{"code": "FINAL", "name": "期末", "value": "90"}],
        },
        revision="v1:detail",
        dependency_revisions={},
        changes={},
        reason="manual",
    )


def test_online_detail_routes_use_typed_course_key_and_hide_wid(tmp_path, monkeypatch):
    from backend.app.routers import scores as scores_router

    store = CacheStore(tmp_path / "cache.db")
    _store_cached_detail(store)
    submissions = []

    class Coordinator:
        def submit(self, **kwargs):
            submissions.append(kwargs)
            return SimpleNamespace(
                status=RefreshStatus.STARTED,
                job_id="job",
                revision="v1:detail",
                is_stale=False,
            )

    monkeypatch.setattr(scores_router, "_cache_store", store)
    monkeypatch.setattr(scores_router, "_cache_coordinator", Coordinator())
    monkeypatch.setattr(scores_router, "read_cache", lambda account, resource: (
        store.get(CacheKey(account, resource)), False
    ))
    monkeypatch.setattr(scores_router, "get_auth_generation", lambda: 7)
    auth = SimpleNamespace(username="account")

    cached = scores_router.get_score_detail_cache("CODE", "2025-2026-2", auth)
    queried = scores_router.query_score_detail(
        ScoreDetailQueryRequest(course_code="CODE", term="2025-2026-2"),
        auth,
    )

    assert cached.item_scores[0].name == "期末"
    assert "wid" not in str(cached.model_dump(by_alias=True)).lower()
    assert queried["job_id"] == "job"
    assert submissions == [{
        "account_id": "account",
        "resource": "score-details",
        "variant": score_detail_variant("CODE", "2025-2026-2"),
        "identity_epoch": 7,
        "force": True,
        "reason": "manual",
    }]


def test_offline_detail_route_only_reads_cache(tmp_path, monkeypatch):
    from backend.app.routers import offline as offline_router

    store = CacheStore(tmp_path / "cache.db")
    _store_cached_detail(store)
    monkeypatch.setattr(offline_router, "_cache_store", store)
    monkeypatch.setattr(offline_router, "_offline_account", lambda: "account")

    cached = offline_router.offline_score_details("CODE", "2025-2026-2")

    assert cached.course_code == "CODE"
    assert cached.item_scores[0].value == "90"
    assert "wid" not in str(cached.model_dump(by_alias=True)).lower()


def test_all_scores_aborts_when_any_term_response_is_invalid(monkeypatch):
    client = FakeClient(posts=[
        FakeResponse({"code": "0", "datas": {"cxwdcj": {"rows": []}}}),
        FakeResponse({"code": "1", "datas": {}}),
    ])
    api = AcademicAPI(client)
    monkeypatch.setattr(
        api,
        "get_terms",
        lambda: [
            {"code": "2025-2026-2", "name": "second"},
            {"code": "2025-2026-1", "name": "first"},
        ],
    )
    monkeypatch.setattr(academic_module.time, "sleep", lambda _seconds: None)

    with pytest.raises(AcademicDataError):
        api.get_scores()

    assert len(client.post_calls) == 2


@pytest.mark.parametrize(
    "payload",
    [
        {"code": "1", "datas": {}},
        {"code": "0", "datas": {"cxwdcjxnxq": {"rows": None}}},
    ],
)
def test_terms_reject_business_error_and_malformed_rows(payload):
    with pytest.raises(AcademicDataError):
        AcademicAPI(FakeClient(gets=[FakeResponse(payload)])).get_terms()


def test_terms_allow_legitimate_empty_result():
    result = AcademicAPI(FakeClient(gets=[FakeResponse({
        "code": "0", "datas": {"cxwdcjxnxq": {"rows": []}}
    })])).get_terms()

    assert result == []
