from types import SimpleNamespace

from backend.app.routers import experiment


class _Registry:
    @staticmethod
    def resources():
        return ("academic-report",)


class _Coordinator:
    registry = _Registry()

    def __init__(self):
        self.invalidated = []

    def invalidate(self, **values):
        self.invalidated.append(values)


def _request():
    return {
        "term": "2025-2026-2",
        "task_id": "task",
        "project_code": "project",
        "round_id": "round",
    }


def test_experiment_success_invalidates_declared_cache(monkeypatch):
    coordinator = _Coordinator()
    monkeypatch.setattr(experiment, "_cache_coordinator", coordinator)
    monkeypatch.setattr(
        experiment,
        "ExperimentCourseAPI",
        lambda _auth: SimpleNamespace(select=lambda *_args: {"code": "0"}),
    )

    result = experiment.select_experiment_course(
        _request(), SimpleNamespace(username="20250001")
    )

    assert result["code"] == "0"
    assert coordinator.invalidated == [{
        "account_id": "20250001",
        "resource": "academic-report",
    }]


def test_experiment_failure_does_not_invalidate_cache(monkeypatch):
    coordinator = _Coordinator()
    monkeypatch.setattr(experiment, "_cache_coordinator", coordinator)
    monkeypatch.setattr(
        experiment,
        "ExperimentCourseAPI",
        lambda _auth: SimpleNamespace(
            deselect=lambda *_args: {"code": "-1", "message": "failed"}
        ),
    )

    result = experiment.deselect_experiment_course(
        _request(), SimpleNamespace(username="20250001")
    )

    assert result["code"] == "-1"
    assert coordinator.invalidated == []
