from types import SimpleNamespace

import pytest

from backend.core.academic.gpa_policy import GpaPolicyService, calculate_gpa, default_mode
from backend.core.cache import CacheKey, CacheStore, PayloadType
from backend.core.tracking.service import GradeTrackingService


@pytest.mark.parametrize("account,expected", [
    ("20240001", "through_2024"), ("20250001", "from_2025"), ("20260001", "from_2025"),
    ("20300001", "from_2025"), ("fixture", "through_2024"), ("", "through_2024"),
])
def test_default_policy_by_first_four_digits(account, expected):
    assert default_mode(account) == expected


def put(store, account, resource, payload, variant="default"):
    store.commit_success(
        key=CacheKey(account, resource, variant), schema_version=1,
        revision_algorithm_version=1, payload_type=PayloadType.JSON, payload=payload,
        revision="test", dependency_revisions={}, changes={}, reason="test",
    )


@pytest.fixture
def service(tmp_path):
    store = CacheStore(tmp_path / "cache.db")
    result = GpaPolicyService(tmp_path, store)
    put(store, "20250001", "academic-report", {"categories": [{
        "name": "通识类", "children": [
            {"name": "通识选修类", "children": [{"name": "深层分类", "courses": [{"course_code": "GE"}]}]},
            {"name": "通识必修类", "courses": [{"course_code": "CORE"}]},
        ],
    }]})
    put(store, "20250001", "course-outline-metadata", {"grading_scale": "两级制"}, "course:BIN")
    for code in ("CORE", "FAIL"):
        put(store, "20250001", "course-outline-metadata", {"grading_scale": "百分制"}, f"course:{code}")
    put(store, "20250001", "scores", {"scores": [
        {"code": "CORE", "gpa": 4, "credit": 3},
        {"code": "FAIL", "gpa": 0, "credit": 2},
        {"code": "GE", "gpa": 5, "credit": 2},
        {"code": "BIN", "gpa": 5, "credit": 1},
    ]})
    return result


def test_policy_is_account_owned_persistent_and_resettable(service):
    assert service.preference("20250001")["mode"] == "from_2025"
    service.save("20250001", "through_2024")
    restarted = GpaPolicyService(service.root.parent, service.store)
    assert restarted.preference("20250001")["mode"] == "through_2024"
    assert restarted.preference("20260001")["mode"] == "from_2025"
    assert restarted.save("20250001", None)["override"] is None
    with pytest.raises(ValueError):
        service.save("20250001", "invalid")


def test_nested_general_elective_and_binary_excluded_but_zero_gpa_included(service):
    scores = service.store.get(CacheKey("20250001", "scores")).payload["scores"]
    modern = service.summarize("20250001", scores)
    assert modern["average"] == 2.4
    assert modern["credits"] == 5
    assert modern["count"] == 2
    assert modern["policy"]["general_elective_codes"] == ["GE"]
    assert modern["policy"]["missing_grading_scales"] == 0
    service.save("20250001", "through_2024")
    assert service.summarize("20250001", scores)["average"] == 27 / 8
    assert len(scores) == 4


def test_empty_invalid_and_unknown_metadata(service):
    context = service.context("20260001")
    assert not context["report_available"]
    assert calculate_gpa([{"gpa": 0, "credit": 1}], context)["average"] == 0
    assert calculate_gpa([{"gpa": float("nan"), "credit": 1}, {"gpa": 2, "credit": 0}], context)["average"] is None
    assert service.summarize("20260001", [{"code": "UNKNOWN", "gpa": 3, "credit": 1}])["policy"]["missing_grading_scales"] == 1


def test_incompatible_cache_does_not_supply_calculation_context(service):
    service.registry = SimpleNamespace(get=lambda _: SimpleNamespace(
        schema_version=2, revision_algorithm_version=1, payload_type=PayloadType.JSON,
    ))
    context = service.context("20250001")
    assert context["general_elective_codes"] == []
    assert context["grading_scales"] == {}
    assert not context["report_available"]


def test_policy_request_rejects_unknown_mode_and_fields():
    from backend.app.routers.gpa import GpaPolicyRequest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        GpaPolicyRequest(mode="unknown")
    with pytest.raises(ValidationError):
        GpaPolicyRequest(mode="from_2025", username="different-account")


def test_routes_offline_and_notifications_share_saved_policy(service, monkeypatch):
    from backend.app.routers import gpa, offline
    monkeypatch.setattr(gpa, "get_gpa_policy", lambda: service)
    monkeypatch.setattr(gpa, "schedule_gpa_context", lambda _: None)
    monkeypatch.setattr(offline, "get_gpa_policy", lambda: service)
    monkeypatch.setattr(offline, "_offline_account", lambda: "20250001")
    auth = SimpleNamespace(username="20250001")
    assert gpa.get_policy(auth)["mode"] == offline.offline_gpa_policy()["mode"]
    tracker = GradeTrackingService.__new__(GradeTrackingService)
    tracker._state = {"account_id": "20250001"}
    tracker.gpa_summary_provider = service.summarize
    snapshot = {"courses": service.store.get(CacheKey("20250001", "scores")).payload["scores"]}
    assert "2.4000" in tracker._calculated_gpa_text(snapshot)
    gpa.set_policy(gpa.GpaPolicyRequest(mode="through_2024"), auth)
    assert "3.3750" in tracker._calculated_gpa_text(snapshot)
    assert offline.offline_gpa_policy()["mode"] == "through_2024"
