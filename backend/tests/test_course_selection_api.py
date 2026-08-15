from datetime import datetime, timezone

import pytest
from fastapi import HTTPException, Response
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.app.main import app
from backend.app.routers import course_selection
from backend.app.schemas.course_selection import (
    CourseSelectionOptimizeRequest,
    JwxkCourseDeselectRequest,
    JwxkCourseSelectRequest,
    JwxkBatchRequest,
    JwxkCatalogDetailRequest,
    JwxkCatalogSearchRequest,
    JwxkCatalogSearchResponse,
    JwxkEligibilityRequest,
    JwxkPlanPreviewRequest,
    JwxkSavedPlanRequest,
    JwxkWeightPlanRequest,
    JwxkAutomationTaskRequest,
)
from backend.core.auth.client import NEULoginError
from backend.core.course_selection import JwxkError


def _payload():
    return {
        "policy": {"budget": 15, "min_bid": 5, "bid_step": 1},
        "market": {
            "cohort_size": 20,
            "captured_at": datetime(2026, 8, 5, tzinfo=timezone.utc).isoformat(),
            "is_complete": True,
            "courses": [
                {
                    "course_id": "A",
                    "name": "课程A",
                    "capacity": 8,
                    "current_participants": 10,
                    "target_interested": True,
                    "target_utility": 10,
                },
                {
                    "course_id": "B",
                    "name": "课程B",
                    "capacity": 12,
                    "current_participants": 9,
                },
            ],
        },
    }


def test_optimize_route_is_registered_and_stateless(monkeypatch):
    assert "/api/course-selection/optimize" in app.openapi()["paths"]

    def forbidden(*_args, **_kwargs):
        raise AssertionError("stateless optimizer must not access auth or storage")

    monkeypatch.setattr("backend.app.dependencies.peek_auth_client", forbidden)
    request = CourseSelectionOptimizeRequest.model_validate(_payload())
    http_response = Response()
    result = course_selection.optimize_course_selection(request, http_response)
    assert result.model_version == "course-selection-contest-v1"
    assert all(strategy.budget_used == 15 for strategy in result.strategies)
    assert http_response.headers["cache-control"] == "no-store"


def test_real_http_route_serializes_success_and_validation_errors():
    client = TestClient(app)
    success = client.post("/api/course-selection/optimize", json=_payload())
    assert success.status_code == 200
    assert success.headers["cache-control"] == "no-store"
    assert success.json()["model_version"] == "course-selection-contest-v1"

    invalid = _payload()
    invalid["market"]["is_complete"] = False
    rejected = client.post("/api/course-selection/optimize", json=invalid)
    assert rejected.status_code == 422


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value["market"].update(is_complete=False),
        lambda value: value["market"]["courses"].append(dict(value["market"]["courses"][0])),
        lambda value: value["market"]["courses"][0].update(current_participants=21),
        lambda value: value["policy"].update(budget=4, min_bid=5),
        lambda value: value["market"].update(courses=[]),
        lambda value: value["market"].update(captured_at="2026-08-05T12:00:00"),
        lambda value: value["policy"].update(demand_multipliers=[0.8, 1.0, 1e308]),
    ],
)
def test_invalid_http_contract_returns_validation_error(mutate):
    payload = _payload()
    mutate(payload)
    with pytest.raises(ValidationError):
        CourseSelectionOptimizeRequest.model_validate(payload)


def test_unknown_fields_and_non_finite_utility_are_rejected():
    payload = _payload()
    payload["unknown"] = "not allowed"
    with pytest.raises(ValidationError):
        CourseSelectionOptimizeRequest.model_validate(payload)

    payload = _payload()
    payload["market"]["courses"][0]["target_utility"] = float("nan")
    with pytest.raises(ValidationError):
        CourseSelectionOptimizeRequest.model_validate(payload)


def test_model_errors_are_exposed_as_422(monkeypatch):
    request = CourseSelectionOptimizeRequest.model_validate(_payload())
    monkeypatch.setattr(
        course_selection,
        "optimize_course_weights",
        lambda *_args: (_ for _ in ()).throw(course_selection.CourseSelectionError("invalid market")),
    )
    with pytest.raises(HTTPException) as caught:
        course_selection.optimize_course_selection(request, Response())
    assert caught.value.status_code == 422
    assert caught.value.detail == "invalid market"


def test_busy_solver_is_exposed_as_429(monkeypatch):
    class BusySlots:
        @staticmethod
        def acquire(**_kwargs):
            return False

    request = CourseSelectionOptimizeRequest.model_validate(_payload())
    monkeypatch.setattr(course_selection, "_solver_slots", BusySlots())
    with pytest.raises(HTTPException) as caught:
        course_selection.optimize_course_selection(request, Response())
    assert caught.value.status_code == 429


def test_excessive_combined_workload_is_rejected():
    payload = _payload()
    payload["policy"] = {"budget": 150, "min_bid": 5, "bid_step": 1}
    payload["market"]["courses"] = [
        {
            "course_id": f"course-{index}",
            "capacity": 10,
            "current_participants": 10,
            "target_interested": True,
        }
        for index in range(31)
    ]
    with pytest.raises(ValidationError, match="too large"):
        CourseSelectionOptimizeRequest.model_validate(payload)


def test_jwxk_weight_plan_uses_official_budget_and_group_optimizer(monkeypatch):
    class FakeClient:
        def get_weight_budget(self, **_kwargs):
            return {"remaining": 105, "total": 105, "used": 0, "minimum": 5, "step": 1}

    monkeypatch.setattr(course_selection, "_jwxk_mutation_client", lambda *_args: FakeClient())
    class FakeStorage:
        def __init__(self):
            self.value = {}

        def load_config(self):
            return self.value

        def save_config(self, value):
            self.value = value

    monkeypatch.setattr(course_selection, "_weight_market_archive", lambda *_args: {
        "catalog_complete": True,
        "courses": [
            {"class_id": "A-1", "capacity": 30, "weight_participant_count": 25},
            {"class_id": "A-2", "capacity": 30, "weight_participant_count": 20},
            {"class_id": "B-1", "capacity": 40, "weight_participant_count": 35},
            {"class_id": "OTHER", "capacity": 80, "weight_participant_count": 50},
        ],
    })
    request = JwxkWeightPlanRequest.model_validate({
        "batch_code": "weight-batch",
        "term_code": "2026-2027-1",
        "grade_size": 126,
        "groups": [{"group_id": "group-1", "name": "选修组", "target_count": 2}],
        "items": [
            {
                "course_code": "COURSE-A", "course_name": "课程A", "class_id": "A-1",
                "plan_group_id": "group-1", "priority": 1, "utility": 9,
                "capacity": 10, "weight_participant_count": 1,
            },
            {
                "course_code": "COURSE-A", "course_name": "课程A", "class_id": "A-2",
                "plan_group_id": "group-1", "priority": 2, "utility": 9,
                "capacity": 30, "weight_participant_count": 20,
            },
            {
                "course_code": "COURSE-B", "course_name": "课程B", "class_id": "B-1",
                "plan_group_id": "group-1", "priority": 2, "utility": 7,
                "capacity": 40, "weight_participant_count": 35,
            },
        ],
    })

    storage = FakeStorage()
    result = course_selection.plan_jwxk_weights(
        request, Response(), auth=type("Auth", (), {"username": "student"})(), storage=storage,
    )

    assert result["budget"] == 105
    assert result["model_version"].startswith("course-weight-optimizer-d70349b")
    assert result["groups"][0]["satisfied"] is True
    assert {item["course_code"] for item in result["items"]} == {"COURSE-A", "COURSE-B"}
    assert sum(item["weight"] for item in result["items"]) == 105
    by_code = {item["course_code"]: item for item in result["courses"]}
    assert by_code["COURSE-A"]["current_participant_label"] == "已投注人数"
    assert by_code["COURSE-A"]["current_participant_count"] == 25
    assert by_code["COURSE-A"]["current_capacity"] == 30
    assert storage.value["course_selection_weight_grade_sizes"]["student:2026-2027-1"] == 126


def test_jwxk_weight_apply_rejects_duplicate_courses_before_any_write(monkeypatch):
    class FakeClient:
        writes = 0

        def get_weight_budget(self, **_kwargs):
            return {"remaining": 105, "total": 105, "used": 0, "minimum": 5, "step": 1}

        def select_course(self, **_kwargs):
            self.writes += 1
            raise AssertionError("duplicate course must be rejected before mutation")

    client = FakeClient()
    monkeypatch.setattr(course_selection, "_jwxk_mutation_client", lambda *_args: client)
    request = JwxkSavedPlanRequest.model_validate({
        "batch_code": "weight-batch", "term_code": "2026-2027-1", "groups": [],
        "items": [
            {"course_code": "COURSE-A", "class_id": "A-1", "weight": 50},
            {"course_code": "course-a", "class_id": "A-2", "weight": 55},
        ],
    })

    with pytest.raises(HTTPException, match="同一课程只能投放一次"):
        course_selection.apply_jwxk_weights(request, auth=object(), storage=object())

    assert client.writes == 0


@pytest.mark.parametrize(
    "task_type,selection_type_code,error",
    [
        ("weight_strategy", "02", "策略投权只适用于权重选课轮次"),
        ("selection", "04", "自动抢课和空位追踪只适用于抢选轮次"),
        ("vacancy_swap", "04", "自动抢课和空位追踪只适用于抢选轮次"),
    ],
)
def test_automation_task_type_cannot_cross_round_modes(monkeypatch, task_type, selection_type_code, error):
    class Service:
        def list_catalog_archives(self, _account):
            return [{"batch_code": "batch", "selection_type_code": selection_type_code}]

        def create(self, *_args):
            raise AssertionError("invalid task must not be persisted")

    monkeypatch.setattr(course_selection, "get_course_selection_automation_service", lambda: Service())
    payload = {
        "batch_code": "batch", "term_code": "2026-2027-1", "task_type": task_type,
        "name": "task", "groups": [], "items": [],
    }
    if task_type == "weight_strategy":
        payload["grade_size"] = 126
    request = JwxkAutomationTaskRequest.model_validate(payload)

    with pytest.raises(HTTPException, match=error):
        course_selection.create_jwxk_automation_task(
            request,
            auth=type("Auth", (), {"username": "student"})(),
            storage=object(),
        )


def test_large_aggregate_market_within_grid_limit_is_accepted():
    payload = _payload()
    payload["policy"] = {"budget": 150, "min_bid": 5, "bid_step": 1}
    payload["market"]["cohort_size"] = 200
    payload["market"]["courses"] = [
        {
            "course_id": f"course-{index}",
            "capacity": 50,
            "current_participants": 100,
            "target_interested": index == 0,
        }
        for index in range(20)
    ]
    request = CourseSelectionOptimizeRequest.model_validate(payload)
    assert len(request.market.courses) == 20


def test_jwxk_select_invalidates_personal_timetable_only_after_success(monkeypatch):
    class FakeClient:
        def select_course(self, **kwargs):
            assert kwargs["class_id"] == "CLASS-1"
            assert kwargs["course_code"] == "COURSE-1"
            assert kwargs["confirm_risk"] is True
            return {
                "success": True,
                "queued": True,
                "requires_confirmation": False,
                "code": "200",
                "message": "已进入官方处理队列",
                "_term_code": "2026-2027-1",
            }

    invalidations = []
    monkeypatch.setattr(course_selection, "_jwxk_mutation_client", lambda *_args: FakeClient())
    monkeypatch.setattr(
        course_selection,
        "_invalidate_jwxk_timetable",
        lambda auth, term_code, operation: invalidations.append((auth.username, term_code, operation)),
    )
    auth = type("Auth", (), {"username": "student"})()
    result = course_selection.select_jwxk_course(
        JwxkCourseSelectRequest(
            batch_code="BATCH-1",
            teaching_class_type="ALLKC",
            class_id="CLASS-1",
            course_code="COURSE-1",
            confirm_risk=True,
        ),
        Response(),
        auth,
        object(),
    )

    assert result.success is True
    assert result.queued is True
    assert invalidations == [("student", "2026-2027-1", "jwxk.select")]


def test_jwxk_deselect_rejection_does_not_invalidate_cache(monkeypatch):
    class FakeClient:
        def deselect_course(self, **_kwargs):
            raise JwxkError("当前不在该轮次的选课时间内")

    monkeypatch.setattr(course_selection, "_jwxk_mutation_client", lambda *_args: FakeClient())
    monkeypatch.setattr(
        course_selection,
        "_invalidate_jwxk_timetable",
        lambda *_args: (_ for _ in ()).throw(AssertionError("rejected mutation must not invalidate cache")),
    )
    auth = type("Auth", (), {"username": "student"})()

    with pytest.raises(HTTPException) as caught:
        course_selection.deselect_jwxk_course(
            JwxkCourseDeselectRequest(
                batch_code="BATCH-1",
                class_id="CLASS-1",
                confirm_risk=True,
            ),
            Response(),
            auth,
            object(),
        )

    assert caught.value.status_code == 409
    assert caught.value.detail == "当前不在该轮次的选课时间内"


def test_jwxk_eligibility_route_returns_only_sanitized_result(monkeypatch):
    class FakeClient:
        def check_course_eligibility(self, **kwargs):
            assert kwargs == {"batch_code": "BATCH-1", "class_ids": ["CLASS-1"]}
            return {"results": [{
                "class_id": "CLASS-1",
                "status": "unavailable",
                "reason": "轮次中未找到该教学班",
            }]}

    monkeypatch.setattr(
        course_selection,
        "_run_jwxk_read",
        lambda _storage, operation: operation(FakeClient()),
    )
    response = Response()
    result = course_selection.check_jwxk_catalog_eligibility(
        JwxkEligibilityRequest(batch_code="BATCH-1", class_ids=["CLASS-1"]),
        response,
        object(),
    )

    assert result.results[0].status == "unavailable"
    assert result.results[0].reason == "轮次中未找到该教学班"
    assert response.headers["cache-control"] == "no-store"


def test_jwxk_catalog_response_accepts_group_source_metadata_on_classes():
    response = JwxkCatalogSearchResponse.model_validate({
        "total": 1,
        "scope": "ALL",
        "scope_options": [{"value": "ALL", "label": "所有课程"}],
        "groups": [{
            "group_id": "jwxk-course-example",
            "course_code": "COURSE-1",
            "course_name": "示例课程",
            "source_tags": ["培养方案内课", "本轮课程"],
            "classes": [{
                "group_id": "jwxk-course-example",
                "course_code": "COURSE-1",
                "course_name": "示例课程",
                "class_id": "CLASS-1",
                "source_tags": ["培养方案内课", "本轮课程"],
                "source_scopes": ["ROUND", "FANKC"],
            }],
        }],
    })

    item = response.groups[0].classes[0]
    assert item.group_id == "jwxk-course-example"
    assert item.source_tags == ["培养方案内课", "本轮课程"]
    assert item.source_scopes == ["ROUND", "FANKC"]


def test_catalog_search_uses_complete_archive_for_specific_scope_filters(monkeypatch):
    captured = {}

    class Automation:
        def merge_catalog_archive(self, *_args, **_kwargs):
            return None

        def schedule_catalog_sync(self, *_args, **_kwargs):
            return None

        def query_catalog_archive(self, _account, **kwargs):
            captured.update(kwargs)
            return {
                "total": 1, "sync_status": "complete", "groups": [{
                    "group_id": "COURSE-1", "course_code": "COURSE-1",
                    "course_name": "用户体验", "course_category": "专业方向类",
                    "classes": [{
                        "group_id": "COURSE-1", "course_code": "COURSE-1",
                        "course_name": "用户体验", "class_id": "CLASS-1",
                        "teaching_class_type": "TJKC", "course_category": "专业方向类",
                    }],
                }],
            }

    monkeypatch.setattr(course_selection, "get_course_selection_automation_service", lambda: Automation())
    monkeypatch.setattr(course_selection, "_run_jwxk_read", lambda *_args: {
        "_account": "student", "_batch": {"code": "BATCH-1"},
        "total": 0, "groups": [], "scope": "TJKC",
        "scope_options": [{"code": "TJKC", "name": "任务推荐班课程"}],
    })

    result = course_selection.search_jwxk_catalog(
        JwxkCatalogSearchRequest(
            batch_code="BATCH-1", scope="TJKC", filters={"KCLB": "专业方向类"},
        ),
        Response(),
        object(),
    )

    assert captured["scope"] == "TJKC"
    assert captured["filters"] == {"KCLB": "专业方向类"}
    assert result.total == 1
    assert result.groups[0].course_name == "用户体验"
    assert result.groups[0].classes[0].teaching_class_type == "TJKC"


def test_catalog_local_search_returns_archive_without_remote_request(monkeypatch):
    class Automation:
        def get_catalog_archive_view(self, account, batch_code):
            assert account == "student"
            assert batch_code == "BATCH-1"
            return {
                "batch_code": "BATCH-1", "sync_status": "complete",
                "courses": [{
                    "course_code": "COURSE-1", "course_name": "本地课程",
                    "class_id": "CLASS-1", "teaching_class_type": "TJKC",
                    "source_scopes": ["TJKC", "ALLKC"],
                }],
            }

        def query_catalog_archive(self, account, **kwargs):
            assert account == "student"
            assert kwargs["keyword"] == "本地"
            return {
                "total": 1, "sync_status": "complete", "groups": [{
                    "group_id": "COURSE-1", "course_code": "COURSE-1",
                    "course_name": "本地课程", "classes": [{
                        "group_id": "COURSE-1", "course_code": "COURSE-1",
                        "course_name": "本地课程", "class_id": "CLASS-1",
                        "teaching_class_type": "TJKC",
                    }],
                }],
            }

    monkeypatch.setattr(course_selection, "get_course_selection_automation_service", lambda: Automation())
    monkeypatch.setattr(course_selection, "peek_auth_client", lambda: type(
        "Auth", (), {"is_logged_in": True, "username": "student"}
    )())
    monkeypatch.setattr(
        course_selection, "_run_jwxk_read",
        lambda *_args: (_ for _ in ()).throw(AssertionError("local search must not call JWXK")),
    )

    result = course_selection.search_jwxk_catalog(
        JwxkCatalogSearchRequest(
            batch_code="BATCH-1", keyword="本地", local_only=True,
        ),
        Response(),
        object(),
    )

    assert result.cache_hit is True
    assert result.data_source == "local"
    assert result.groups[0].course_name == "本地课程"
    assert {item["code"] for item in result.scope_options} >= {"ALL", "ROUND", "TJKC", "ALLKC"}


def test_catalog_filter_options_use_archive_without_remote_request(monkeypatch):
    archive = {
        "batch_code": "BATCH-1",
        "courses": [{
            "course_code": "COURSE-1", "course_name": "本地课程",
            "class_id": "CLASS-1", "teaching_class_type": "TJKC",
            "course_nature": "选修", "course_category": "专业方向类",
            "campus": "01", "campus_name": "浑南校区", "schedules": [],
        }],
    }

    class Automation:
        def get_catalog_archive_view(self, account, batch_code):
            assert account == "student"
            assert batch_code == "BATCH-1"
            return archive

    monkeypatch.setattr(course_selection, "get_course_selection_automation_service", lambda: Automation())
    monkeypatch.setattr(
        course_selection, "_run_jwxk_read",
        lambda *_args: (_ for _ in ()).throw(AssertionError("local options must not call JWXK")),
    )

    result = course_selection.get_jwxk_catalog_filter_options(
        JwxkBatchRequest(batch_code="BATCH-1"),
        Response(),
        type("Auth", (), {"username": "student"})(),
        object(),
    )

    assert result["course_natures"] == [{"value": "选修", "label": "选修"}]
    assert result["course_categories"] == [{"value": "专业方向类", "label": "专业方向类"}]
    assert result["campuses"] == [{"value": "01", "label": "浑南校区"}]


def test_jwxk_catalog_detail_route_returns_sanitized_course_and_class(monkeypatch):
    class FakeClient:
        def get_catalog_detail(self, **kwargs):
            assert kwargs == {
                "batch_code": "BATCH-1", "teaching_class_type": "FANKC",
                "course_code": "COURSE-1", "class_id": "CLASS-1",
            }
            return {
                "course": {
                    "course_code": "COURSE-1", "course_name": "示例课程",
                    "exam_type_code": "01", "exam_type": "考试",
                    "score_scale_code": "100", "score_scale": "百分制",
                },
                "teaching_class": {
                    "course_code": "COURSE-1", "course_name": "示例课程",
                    "class_id": "CLASS-1", "teacher": "教师甲",
                    "teacher_details": [{"name": "教师甲", "teacher_id": "T1", "title": "教授"}],
                    "target_classes": ["班级一"],
                },
            }

    monkeypatch.setattr(
        course_selection,
        "_run_jwxk_read",
        lambda _storage, operation: operation(FakeClient()),
    )
    response = Response()
    result = course_selection.get_jwxk_catalog_detail(
        JwxkCatalogDetailRequest(
            batch_code="BATCH-1", teaching_class_type="FANKC",
            course_code="COURSE-1", class_id="CLASS-1",
        ),
        response,
        object(),
        type("Auth", (), {"username": "student"})(),
    )

    assert result.course.exam_type == "考试"
    assert result.course.score_scale == "百分制"
    assert result.teaching_class.teacher_details[0]["title"] == "教授"
    assert response.headers["cache-control"] == "no-store"


def test_jwxk_catalog_detail_falls_back_to_round_archive(monkeypatch):
    monkeypatch.setattr(
        course_selection,
        "_run_jwxk_read",
        lambda *_args: (_ for _ in ()).throw(HTTPException(status_code=502, detail="changed")),
    )

    class Automation:
        def list_catalog_archives(self, account):
            assert account == "student"
            return [{
                "batch_code": "BATCH-1",
                "courses": [{
                    "course_code": "COURSE-1", "course_name": "归档课程",
                    "class_id": "CLASS-1", "exam_type": "01", "score_scale": "百分制",
                    "course_category": "通识选修课", "general_elective_category": "科学素养类",
                    "general_elective_category_code": "01", "teacher": "教师甲",
                }],
            }]

    monkeypatch.setattr(
        course_selection, "get_course_selection_automation_service", lambda: Automation(),
    )
    result = course_selection.get_jwxk_catalog_detail(
        JwxkCatalogDetailRequest(
            batch_code="BATCH-1", teaching_class_type="FANKC",
            course_code="COURSE-1", class_id="CLASS-1",
        ),
        Response(),
        object(),
        type("Auth", (), {"username": "student"})(),
    )

    assert result.course.exam_type == "考试"
    assert result.course.normalized_course_category == "通识选修"
    assert result.course.general_elective_category == "科学素养类"
    assert result.course.general_elective_category_code == "01"
    assert result.teaching_class.class_id == "CLASS-1"


def test_catalog_filter_options_reuse_complete_round_archive_values():
    merged = course_selection._merge_catalog_option_values({
        "course_categories": [{"value": "专业基础课", "label": "专业基础课"}],
        "course_natures": [{"value": "必修", "label": "必修"}],
        "general_elective_categories": [],
    }, {
        "courses": [
            {"course_category": "通识选修课", "general_elective_category": "科学素养类", "course_nature": "选修", "campus": "浑南校区"},
            {"course_category": "专业基础课", "course_nature": "必修", "department": "机械学院"},
        ],
    })

    assert [item["value"] for item in merged["course_categories"]] == ["专业基础课", "通识选修课"]
    assert [item["value"] for item in merged["course_natures"]] == ["必修", "选修"]
    assert merged["general_elective_categories"] == [{"value": "科学素养类", "label": "科学素养类"}]
    assert merged["campuses"] == [{"value": "01", "label": "浑南校区"}]
    assert merged["departments"] == [{"value": "机械学院", "label": "机械学院"}]


def test_catalog_search_accepts_chinese_legacy_campus_without_422():
    request = JwxkCatalogSearchRequest(
        batch_code="BATCH-1", scope="ALL", campus="浑南校区",
    )

    assert request.campus == "浑南校区"


def test_jwxk_mutation_client_recovers_service_session_before_write_lookup():
    calls = []

    class Auth:
        active_mode = "direct"

        def ensure_service_session(self, service, *, network_mode_override):
            calls.append((service, network_mode_override))
            return True

    class Storage:
        def load_config(self):
            return {"course_selection": {"network_mode": "follow"}}

    client = course_selection._jwxk_mutation_client(Auth(), Storage())

    assert calls == [("jwxk", "direct")]
    assert client.network_mode == "direct"


def test_jwxk_mutation_expired_login_is_exposed_as_401(monkeypatch):
    class FakeClient:
        def select_course(self, **_kwargs):
            raise NEULoginError("统一认证会话已过期")

    monkeypatch.setattr(course_selection, "_jwxk_mutation_client", lambda *_args: FakeClient())
    auth = type("Auth", (), {"username": "student"})()

    with pytest.raises(HTTPException) as caught:
        course_selection.select_jwxk_course(
            JwxkCourseSelectRequest(
                batch_code="BATCH-1",
                teaching_class_type="ALLKC",
                class_id="CLASS-1",
                course_code="COURSE-1",
                confirm_risk=True,
            ),
            Response(),
            auth,
            object(),
        )

    assert caught.value.status_code == 401
    assert caught.value.detail == "统一认证会话已过期"


def test_jwxk_plan_preview_uses_cached_personal_timetable(monkeypatch):
    entry = type("Entry", (), {"payload": {
        "term_code": "2026-2027-1",
        "courses": [{
                "id": "mine", "course_code": "MATH", "course_name": "高等数学",
                "weeks": [1, 2], "weekday": 1, "start_section": 1, "end_section": 2,
        }],
    }})()

    monkeypatch.setattr(
        course_selection,
        "get_cache_coordinator",
        lambda: type("Coordinator", (), {
            "read": lambda self, **kwargs: (entry, False),
        })(),
    )
    auth = type("Auth", (), {"username": "student"})()
    response = course_selection.preview_jwxk_plan(
        JwxkPlanPreviewRequest(
            batch_code="BATCH-1", term_code="2026-2027-1",
            meetings=[{
                "candidate_id": "class-1", "course_code": "PHYS", "course_name": "大学物理",
                "weeks": [2, 3], "weekday": 1, "start_section": 2, "end_section": 3,
            }],
        ),
        auth,
    )
    assert response.baseline_available is True
    assert response.baseline_stale is False
    assert response.results[0]["status"] == "conflict"
    assert response.results[0]["matches"][0]["baseline_course_name"] == "高等数学"


def test_jwxk_plan_read_returns_archived_batch_snapshot_without_remote_status(monkeypatch):
    class Storage:
        def load_config(self):
            return {"course_selection_plans": {}}

    class Automation:
        def get_catalog_archive_view(self, account, batch_code):
            assert account == "student"
            assert batch_code == "BATCH-1"
            return {
                "batch_code": "BATCH-1",
                "batch_name": "轮次3 选修课初选",
                "term_code": "2026-2027-1",
                "term_name": "2026-2027学年秋季学期",
                "selection_type_code": "04",
                "begin_time": "2026-08-15 13:00:00",
                "end_time": "2026-08-17 00:00:00",
                "courses": (),
            }

    monkeypatch.setattr(
        course_selection, "get_course_selection_automation_service", lambda: Automation(),
    )
    result = course_selection.read_jwxk_plan(
        JwxkBatchRequest(batch_code="BATCH-1"),
        type("Auth", (), {"username": "student"})(),
        Storage(),
    )

    assert result["term_code"] == "2026-2027-1"
    assert result["batch"] == {
        "code": "BATCH-1",
        "name": "轮次3 选修课初选",
        "term_code": "2026-2027-1",
        "term_name": "2026-2027学年秋季学期",
        "selection_type_code": "04",
        "selection_type": "权重",
        "begin_time": "2026-08-15 13:00:00",
        "end_time": "2026-08-17 00:00:00",
    }
