from datetime import datetime, timezone

import pytest
from fastapi import HTTPException, Response
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.app.main import app
from backend.app.routers import course_selection
from backend.app.schemas.course_selection import CourseSelectionOptimizeRequest


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
