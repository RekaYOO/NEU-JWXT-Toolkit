from fastapi import APIRouter, HTTPException, Depends, Query

from backend.app.dependencies import get_cache_coordinator
from backend.core.auth import NEUAuthClient
from backend.core.academic.experiment import ExperimentCourseAPI
from backend.core.cache import mutation_policy
from backend.core.log import log_application_error
from backend.app.dependencies import require_serialized_auth
from backend.app.schemas import ExperimentCourseMutationRequest

router = APIRouter()


@router.get("/experiment-courses")
def get_experiment_courses(
    term: str = Query(None, description="学年学期代码，如 2025-2026-2"),
    auth: NEUAuthClient = Depends(require_serialized_auth)
):
    """
    获取实验选课课程列表

    - term 不传则自动获取当前学期
    """
    try:
        api = ExperimentCourseAPI(auth)

        # 如果没有传term，获取当前学期
        if not term:
            term = api.get_semester()

        if not term:
            return {"courses": [], "term": "", "total": 0}

        courses = api.get_courses(term)

        return {
            "courses": [
                {
                    "task_id": c.task_id,
                    "course_name": c.course_name,
                    "course_no": c.course_no,
                    "credit": c.credit,
                    "experiment_hours": c.experiment_hours,
                    "center_name": c.center_name,
                    "college_name": c.college_name,
                    "must_do_count": c.must_do_count,
                    "selected_count": c.selected_count,
                    "is_complete": c.is_complete,
                    "projects": [
                        {
                            "project_name": p.project_name,
                            "project_code": p.project_code,
                            "must_do": p.must_do,
                            "selected_round_id": p.selected_round_id,
                            "select_status": p.select_status,
                            "is_selected": bool(p.selected_round_id),
                        }
                        for p in c.projects
                    ]
                }
                for c in courses
            ],
            "term": term or api.get_semester(),
            "total": len(courses),
        }
    except Exception as e:
        error_id = log_application_error("experiment.list_courses", e, 500)
        raise HTTPException(status_code=500, detail=f"获取实验课程失败（错误编号：{error_id}）") from e


@router.get("/experiment-courses/{task_id}/rounds")
def get_experiment_rounds(
    task_id: str,
    course_no: str = Query(..., description="课程号"),
    project_code: str = Query(..., description="实验项目代码"),
    term: str = Query(..., description="学年学期代码"),
    auth: NEUAuthClient = Depends(require_serialized_auth)
):
    """获取实验班列表"""
    try:
        api = ExperimentCourseAPI(auth)

        rounds = api.get_rounds(term, task_id, course_no, project_code)

        return {
            "rounds": [
                {
                    "wid": r.wid,
                    "round_name": r.round_name,
                    "teacher": r.teacher,
                    "selected_count": r.selected_count,
                    "capacity": r.capacity,
                    "is_full": r.is_full,
                    "week": r.week,
                    "day": r.day,
                    "time": r.time,
                    "location": r.location,
                    "select_start": r.select_start,
                    "select_end": r.select_end,
                    "conflict": r.conflict,
                    "selected": r.selected,
                    "can_select": r.can_select,
                }
                for r in rounds
            ],
            "total": len(rounds),
        }
    except Exception as e:
        error_id = log_application_error("experiment.list_rounds", e, 500)
        raise HTTPException(status_code=500, detail=f"获取实验班失败（错误编号：{error_id}）") from e


@router.post("/experiment-courses/select")
def select_experiment_course(
    data: ExperimentCourseMutationRequest,
    auth: NEUAuthClient = Depends(require_serialized_auth)
):
    """
    选择实验班

    请求体:
    {
        "term": "2025-2026-2",
        "task_id": "...",
        "project_code": "...",
        "round_id": "..."
    }
    """
    try:
        policy = mutation_policy("experiment.select")
        api = ExperimentCourseAPI(auth)

        result = api.select(
            data.term,
            data.task_id,
            data.project_code,
            data.round_id
        )
        if str(result.get("code")) == "0":
            coordinator = get_cache_coordinator()
            for resource in policy.invalidations:
                if resource in coordinator.registry.resources():
                    coordinator.invalidate(
                        account_id=str(auth.username),
                        resource=resource,
                    )

        return result
    except Exception as e:
        error_id = log_application_error("experiment.select", e, 500)
        raise HTTPException(status_code=500, detail=f"选课失败（错误编号：{error_id}）") from e


@router.post("/experiment-courses/deselect")
def deselect_experiment_course(
    data: ExperimentCourseMutationRequest,
    auth: NEUAuthClient = Depends(require_serialized_auth)
):
    """
    退选实验班

    请求体:
    {
        "term": "2025-2026-2",
        "task_id": "...",
        "project_code": "...",
        "round_id": "..."
    }
    """
    try:
        policy = mutation_policy("experiment.deselect")
        api = ExperimentCourseAPI(auth)

        result = api.deselect(
            data.term,
            data.task_id,
            data.project_code,
            data.round_id
        )
        if str(result.get("code")) == "0":
            coordinator = get_cache_coordinator()
            for resource in policy.invalidations:
                if resource in coordinator.registry.resources():
                    coordinator.invalidate(
                        account_id=str(auth.username),
                        resource=resource,
                    )

        return result
    except Exception as e:
        error_id = log_application_error("experiment.deselect", e, 500)
        raise HTTPException(status_code=500, detail=f"退课失败（错误编号：{error_id}）") from e
