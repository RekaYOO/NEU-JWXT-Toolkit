from datetime import datetime

import pytest

from backend.core.academic.experiment import (
    CHINA_STANDARD_TIME,
    ExperimentCourse,
    ExperimentProject,
    ExperimentCourseAPI,
    ExperimentCourseError,
    ExperimentRound,
)
from backend.app.routers import experiment as experiment_router


def _round(**overrides):
    values = {
        "wid": "round-1",
        "round_name": "示例实验班",
        "teacher": "教师甲",
        "selected_count": 1,
        "capacity": 20,
        "week": "1-8周",
        "day": "星期一",
        "time": "1-2节",
        "location": "示例楼101",
        "select_start": "2026-08-01 08:00:00",
        "select_end": "2026-08-20 18:00:00",
    }
    values.update(overrides)
    return ExperimentRound(**values)


def test_experiment_round_selection_window_is_timezone_aware():
    round_ = _round()

    assert round_.selection_window_state(datetime(2026, 7, 31, 12, tzinfo=CHINA_STANDARD_TIME)) == "not_started"
    assert round_.selection_window_state(datetime(2026, 8, 9, 12, tzinfo=CHINA_STANDARD_TIME)) == "open"
    assert round_.selection_window_state(datetime(2026, 8, 21, 12, tzinfo=CHINA_STANDARD_TIME)) == "ended"


def test_unknown_selection_window_does_not_falsely_block_remote_validation():
    round_ = _round(select_start="", select_end="")

    assert round_.selection_window_state() == "unknown"
    assert round_.can_select is True


def test_experiment_rounds_merge_cached_personal_timetable_conflicts(monkeypatch):
    round_ = _round(
        week="1-8周",
        day="星期一",
        time="1-2节",
        select_start="",
        select_end="",
    )

    class API:
        def __init__(self, _auth):
            pass

        def get_rounds(self, *_args):
            return [round_]

    entry = type("Entry", (), {
        "payload": {
            "term_code": "2026-2027-1",
            "courses": [{
                "meeting_id": "personal-1",
                "course_name": "高等数学",
                "weeks": [1, 2, 3],
                "weekday": 1,
                "start_section": 2,
                "end_section": 3,
            }],
        },
    })()
    coordinator = type("Coordinator", (), {
        "read": lambda self, **_kwargs: (entry, False),
    })()
    monkeypatch.setattr(experiment_router, "ExperimentCourseAPI", API)
    monkeypatch.setattr(experiment_router, "get_cache_coordinator", lambda: coordinator)

    result = experiment_router.get_experiment_rounds(
        task_id="task-1",
        course_no="COURSE-1",
        project_code="PROJECT-1",
        term="2026-2027-1",
        auth=type("Auth", (), {"username": "student"})(),
    )

    row = result["rounds"][0]
    assert row["conflict_status"] == "conflict"
    assert row["can_select"] is False
    assert row["disabled_reason"] == "与个人课表冲突"
    assert row["conflicts"][0]["course_name"] == "高等数学"
    assert row["conflicts"][0]["overlapping_weeks"] == [1, 2, 3]


def test_stale_personal_timetable_conflict_requires_confirmation(monkeypatch):
    round_ = _round(select_start="", select_end="")

    class API:
        def __init__(self, _auth):
            pass

        def get_rounds(self, *_args):
            return [round_]

    entry = type("Entry", (), {
        "payload": {
            "term_code": "2026-2027-1",
            "courses": [{
                "meeting_id": "personal-1",
                "course_name": "高等数学",
                "weeks": [1, 2],
                "weekday": 1,
                "start_section": 1,
                "end_section": 2,
            }],
        },
    })()
    coordinator = type("Coordinator", (), {
        "read": lambda self, **_kwargs: (entry, True),
    })()
    monkeypatch.setattr(experiment_router, "ExperimentCourseAPI", API)
    monkeypatch.setattr(experiment_router, "get_cache_coordinator", lambda: coordinator)

    result = experiment_router.get_experiment_rounds(
        task_id="task-1",
        course_no="COURSE-1",
        project_code="PROJECT-1",
        term="2026-2027-1",
        auth=type("Auth", (), {"username": "student"})(),
    )

    row = result["rounds"][0]
    assert row["conflict_status"] == "unknown"
    assert row["can_select"] is True
    assert row["conflicts"][0]["course_name"] == "高等数学"


def test_experiment_read_failure_is_not_reported_as_an_empty_list():
    class Client:
        def post(self, *_args, **_kwargs):
            raise RuntimeError("network unavailable")

    api = ExperimentCourseAPI(Client())

    with pytest.raises(ExperimentCourseError):
        api.get_courses("2026-2027-1")
    with pytest.raises(ExperimentCourseError):
        api.get_rounds("2026-2027-1", "task", "course", "project")


def test_experiment_course_read_model_exposes_selected_results():
    course = ExperimentCourse(
        task_id="task-1",
        course_name="实验课程",
        course_no="A100",
        credit=1,
        term_code="2026-2027-1",
        experiment_hours=16,
        center_name="实验中心",
        college_name="学院",
        must_do_count=1,
        projects=[
            ExperimentProject(
                project_name="实验项目",
                project_code="P1",
                course_no="A100",
                must_do=True,
                selected_round_id="R1",
                select_status="已选",
            )
        ],
    )

    assert course.to_dict()["selected_count"] == 1
    assert course.selected_result_rows()[0]["selected_round_id"] == "R1"


def test_official_selected_result_uses_task_course_metadata_when_project_row_omits_it():
    class Client:
        def post(self, url, data=None, **_kwargs):
            assert url.endswith("/api/xkjg/queryStudentAllSelectCourseResult.do")
            assert data == {"XNXQDM": "2026-2027-1"}
            return type("Response", (), {
                "json": lambda self: {
                    "code": "0",
                    "datas": {
                        "queryStudentAllSelectCourseResult": [{
                            "taskId": "task-1",
                            "courseName": "基础工业工程",
                            "courseNo": "A1441000070",
                            "projectResultList": [{
                                "projectName": "人机作业分析",
                                "projectCode": "A1441000070-001",
                            "courseName": None,
                            "courseNo": None,
                            "roundId": "round-1",
                            "roundName": "实验班一",
                            "classTeachers": "张老师",
                            "classWeeks": "2-4周",
                            "classDays": "周二",
                            "classSessions": "第3-4节",
                            "classrooms": "南馆 101",
                            "selectCourseStartDate": "2026-09-01 08:00:00",
                            "selectCourseEndDate": "2026-09-10 18:00:00",
                        }],
                        }],
                    },
                },
            })()

    results = ExperimentCourseAPI(Client()).get_selected_course_results("2026-2027-1")

    assert results[0]["course_name"] == "基础工业工程"
    assert results[0]["course_no"] == "A1441000070"
    assert results[0]["round_name"] == "实验班一"
    assert results[0]["teacher"] == "张老师"
    assert results[0]["week"] == "2-4周"
    assert results[0]["day"] == "周二"
    assert results[0]["time"] == "第3-4节"
    assert results[0]["location"] == "南馆 101"
    assert results[0]["select_start"] == "2026-09-01 08:00:00"
    assert results[0]["select_end"] == "2026-09-10 18:00:00"
