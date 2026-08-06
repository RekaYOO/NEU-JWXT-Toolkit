import ast
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from backend.app.schemas import (
    EvaluationBatchRequest,
    EvaluationSubmitRequest,
    ExperimentCourseMutationRequest,
)
from backend.app.routers import evaluation as evaluation_router
from backend.core.evaluation import api as evaluation_api
from backend.core.evaluation.api import EvaluationAPI
from backend import mock_evaluation


def test_default_evaluation_cycle_is_discovered_from_remote_cycles():
    api = EvaluationAPI(object())
    api.get_cycles = lambda: [
        {"value": "older", "isdefault": 0},
        {"value": "current", "isdefault": 1},
    ]

    assert api.get_default_cycle() == "current"


def test_default_evaluation_cycle_does_not_fall_back_to_a_fixed_term():
    api = EvaluationAPI(object())
    api.get_cycles = lambda: []

    with pytest.raises(RuntimeError, match="default evaluation cycle unavailable"):
        api.get_default_cycle()


@pytest.mark.parametrize(
    "payload",
    [
        {"task_id": "task", "strategy": "unexpected"},
        {"task_id": "task", "delay": 5.1},
        {"task_id": "task", "xspjids": [f"course-{i}" for i in range(51)]},
        {"task_id": "task", "xspjids": ["same", "same"]},
        {"task_id": "task", "unknown": "field"},
    ],
)
def test_batch_evaluation_rejects_unbounded_or_ambiguous_input(payload):
    with pytest.raises(ValidationError):
        EvaluationBatchRequest.model_validate(payload)


def test_submit_evaluation_bounds_text_and_scores():
    valid = EvaluationSubmitRequest(
        task_id="task",
        xspjid="course",
        strategy="custom",
        custom_scores={"indicator": [5, 6]},
        text_results={"comment": "useful feedback"},
    )
    assert valid.custom_scores == {"indicator": [5, 6]}
    assert valid.dry_run is True

    # Preserve the previous contract: the core business validation decides
    # whether a partially populated custom map is sufficient for a target.
    assert EvaluationSubmitRequest(
        task_id="task",
        xspjid="course",
        strategy="custom",
    ).custom_scores is None

    with pytest.raises(ValidationError):
        EvaluationSubmitRequest(
            task_id="task",
            xspjid="course",
            text_results={"comment": "x" * 2_001},
        )

    with pytest.raises(ValidationError):
        EvaluationSubmitRequest(
            task_id="task",
            xspjid="course",
            strategy="custom",
            custom_scores={"indicator": 7},
        )


def test_experiment_mutation_requires_a_strict_typed_contract():
    with pytest.raises(ValidationError):
        ExperimentCourseMutationRequest.model_validate(
            {
                "term": "2025-2026-2",
                "task_id": "task",
                "project_code": "project",
            }
        )

    with pytest.raises(ValidationError):
        ExperimentCourseMutationRequest(
            term="2025-2026-2",
            task_id="task",
            project_code="project",
            round_id="round",
            unexpected="field",
        )


def test_evaluation_core_does_not_bypass_safe_logging_with_print():
    tree = ast.parse(Path(evaluation_api.__file__).read_text(encoding="utf-8"))

    assert not [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "print"
    ]


def test_evaluation_submission_does_not_expose_remote_failure_payload(caplog):
    secret = "remote-secret-payload"

    class Client:
        @staticmethod
        def post(*_args, **_kwargs):
            return type(
                "Response",
                (),
                {"status_code": 500, "json": lambda self: {"message": secret}},
            )()

    api = EvaluationAPI(Client())
    api._jwt_token = "test-token"
    api._jwt_exp = time.time() + 3_600
    api.build_submit_data = lambda *_args, **_kwargs: {"task": {"zpf": 90}}

    result = api.submit_evaluation(
        type("Course", (), {})(),
        type("Target", (), {"indicators": []})(),
    )

    assert result == {"success": False, "message": "评教提交失败"}
    assert secret not in caplog.text


def test_evaluation_submission_returns_only_a_minimal_summary():
    secret = "private-evaluation-text"

    class Client:
        @staticmethod
        def post(*_args, **_kwargs):
            return type(
                "Response",
                (),
                {"status_code": 200, "json": lambda self: "success"},
            )()

    api = EvaluationAPI(Client())
    api._jwt_token = "test-token"
    api._jwt_exp = time.time() + 3_600
    api.build_submit_data = lambda *_args, **_kwargs: {
        "task": {"zpf": 90, "secret": secret},
        "resultList": [{"result": secret}],
    }
    api._validate_scoring = lambda _scored: {"valid": True, "errors": []}

    result = api.submit_evaluation(
        type("Course", (), {})(),
        type("Target", (), {"indicators": []})(),
    )

    assert result == {
        "success": True,
        "message": "评教提交成功",
        "summary": {"average_score": 90, "indicator_count": 1},
    }
    assert secret not in repr(result)


def test_evaluation_preview_never_calls_remote_submission():
    class Client:
        @staticmethod
        def post(*_args, **_kwargs):
            raise AssertionError("preview must not write remotely")

    api = EvaluationAPI(Client())
    api.build_submit_data = lambda *_args, **_kwargs: {
        "resultList": [{"evaltype": 1}, {"evaltype": 2}],
        "task": {"zpf": 95.0},
    }
    api._validate_scoring = lambda _scored: {"valid": True, "errors": []}

    result = api.preview_evaluation(
        type("Course", (), {})(),
        type("Target", (), {"indicators": []})(),
    )

    assert result == {
        "success": True,
        "dry_run": True,
        "message": "安全模式预览完成，未向教务系统提交",
        "preview": {
            "average_score": 95.0,
            "indicator_count": 2,
            "selection_count": 1,
            "text_count": 1,
        },
    }


@pytest.mark.parametrize(
    ("dry_run", "expected_operation", "expects_refetches"),
    [
        (True, "preview", False),
        (False, "submit", True),
    ],
)
def test_submit_route_requires_explicit_opt_in_for_remote_write(
    monkeypatch,
    dry_run,
    expected_operation,
    expects_refetches,
):
    operations = []
    course = SimpleNamespace(
        xspjid="course",
        xnxqid="cycle",
        course_name="Software Engineering",
        teacher_name="Teacher",
    )
    target = SimpleNamespace(indicators=[])

    class FakeEvaluationAPI:
        def __init__(self, _auth):
            pass

        @staticmethod
        def get_courses(_task_id):
            return [course]

        @staticmethod
        def get_evaluation_target(_task_id, _xspjid, _xnxqid):
            return target

        @staticmethod
        def preview_evaluation(*_args, **_kwargs):
            operations.append("preview")
            return {"success": True, "dry_run": True}

        @staticmethod
        def submit_evaluation(*_args, **_kwargs):
            operations.append("submit")
            return {"success": True}

    monkeypatch.setattr(evaluation_router, "EVAL_TEST_MODE", 0)
    monkeypatch.setattr(evaluation_router, "EvaluationAPI", FakeEvaluationAPI)

    result = evaluation_router.submit_evaluation(
        EvaluationSubmitRequest(
            task_id="task",
            xspjid="course",
            dry_run=dry_run,
        ),
        auth=object(),
    )

    assert operations == [expected_operation]
    assert bool(result["refetches"]) is expects_refetches


def test_mock_batch_respects_state_written_by_single_submit():
    original = dict(mock_evaluation._test_eval_states)
    course = next(item for item in mock_evaluation.MOCK_COURSES if not item["is_evaluated"])
    try:
        submit_result = mock_evaluation.mock_submit(
            EvaluationSubmitRequest(
                task_id=course["task_id"],
                xspjid=course["xspjid"],
                dry_run=False,
            )
        )
        batch_result = mock_evaluation.mock_batch(
            EvaluationBatchRequest(
                task_id=course["task_id"],
                xspjids=[course["xspjid"]],
                dry_run=False,
            )
        )

        assert submit_result["success"] is True
        assert batch_result["total"] == 0
    finally:
        mock_evaluation._test_eval_states.clear()
        mock_evaluation._test_eval_states.update(original)


def test_mock_router_preserves_real_submit_refetch_contract(monkeypatch):
    monkeypatch.setattr(evaluation_router, "EVAL_TEST_MODE", 1)
    monkeypatch.setattr(
        evaluation_router,
        "mock_submit",
        lambda _request: {"success": True, "dry_run": False},
    )
    monkeypatch.setattr(
        evaluation_router,
        "mock_batch",
        lambda _request: {"success_count": 1, "dry_run": False},
    )

    single = evaluation_router.submit_evaluation(
        EvaluationSubmitRequest(
            task_id="task",
            xspjid="course",
            dry_run=False,
        ),
        auth=object(),
    )
    batch = evaluation_router.batch_evaluation(
        EvaluationBatchRequest(task_id="task", dry_run=False),
        auth=object(),
    )

    assert single["refetches"] == ("evaluation-tasks", "evaluation-courses")
    assert batch["refetches"] == ("evaluation-tasks", "evaluation-courses")
