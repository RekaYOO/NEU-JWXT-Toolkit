from types import SimpleNamespace

from backend.app.routers import experiment
from backend.app.schemas import ExperimentCourseMutationRequest
from backend.core.academic.experiment import ExperimentCourseAPI


class _Registry:
    @staticmethod
    def resources():
        return ("academic-report", "personal-timetable")


class _Coordinator:
    registry = _Registry()

    def __init__(self):
        self.invalidated = []
        self.submitted = []

    def invalidate(self, **values):
        self.invalidated.append(values)

    def submit(self, **values):
        self.submitted.append(values)


def _request():
    return ExperimentCourseMutationRequest(
        term="2025-2026-2",
        task_id="task",
        project_code="project",
        round_id="round",
    )


def _auth(current_term="2025-2026-2"):
    timetable = SimpleNamespace(get_terms=lambda: [
        {"code": current_term, "current": True}
    ])
    return SimpleNamespace(username="20250001", timetable=timetable)


def test_experiment_success_invalidates_declared_cache(monkeypatch):
    coordinator = _Coordinator()
    monkeypatch.setattr(experiment, "get_cache_coordinator", lambda: coordinator)
    monkeypatch.setattr(experiment, "get_auth_generation", lambda: 17)
    monkeypatch.setattr(
        experiment,
        "ExperimentCourseAPI",
        lambda _auth: SimpleNamespace(select=lambda *_args: {"code": "0"}),
    )

    result = experiment.select_experiment_course(
        _request(), _auth()
    )

    assert result["code"] == "0"
    assert coordinator.invalidated == [
        {"account_id": "20250001", "resource": "academic-report"},
        {
            "account_id": "20250001",
            "resource": "personal-timetable",
            "variant": "term:2025-2026-2",
        },
    ]
    assert coordinator.submitted == [
        {
            "account_id": "20250001",
            "resource": "personal-timetable",
            "variant": "term:2025-2026-2",
            "identity_epoch": 17,
            "force": True,
            "reason": "foreground_mutation",
        }
    ]


def test_experiment_failure_does_not_invalidate_cache(monkeypatch):
    coordinator = _Coordinator()
    monkeypatch.setattr(experiment, "get_cache_coordinator", lambda: coordinator)
    monkeypatch.setattr(
        experiment,
        "ExperimentCourseAPI",
        lambda _auth: SimpleNamespace(
            deselect=lambda *_args: {"code": "-1", "message": "failed"}
        ),
    )

    result = experiment.deselect_experiment_course(
        _request(), _auth()
    )

    assert result["code"] == "-1"
    assert coordinator.invalidated == []
    assert coordinator.submitted == []


def test_historical_experiment_success_does_not_rebuild_timetable_cache(monkeypatch):
    coordinator = _Coordinator()
    monkeypatch.setattr(experiment, "get_cache_coordinator", lambda: coordinator)
    monkeypatch.setattr(experiment, "get_auth_generation", lambda: 17)
    monkeypatch.setattr(
        experiment,
        "ExperimentCourseAPI",
        lambda _auth: SimpleNamespace(select=lambda *_args: {"code": "0"}),
    )

    result = experiment.select_experiment_course(
        _request(), _auth(current_term="2026-2027-1")
    )

    assert result["code"] == "0"
    assert coordinator.submitted == []


def test_experiment_remote_exception_is_not_returned_to_caller():
    class Client:
        def post(self, *_args, **_kwargs):
            raise RuntimeError("ticket=secret-value")

    api = ExperimentCourseAPI(Client())

    selected = api.select("term", "task", "project", "round")
    deselected = api.deselect("term", "task", "project", "round")

    assert selected == {"code": "-1", "msg": "远端选课请求失败"}
    assert deselected == {"code": "-1", "msg": "远端退课请求失败"}
    assert "secret-value" not in str(selected) + str(deselected)
