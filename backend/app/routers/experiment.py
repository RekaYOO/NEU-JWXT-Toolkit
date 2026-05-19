from typing import Dict
from fastapi import APIRouter, HTTPException, Depends, Query

from backend.app.dependencies import _api_logger
from backend.core.auth import NEUAuthClient
from backend.core.academic.experiment import ExperimentCourseAPI
from backend.app.dependencies import require_auth

router = APIRouter()


@router.get("/experiment-courses")
async def get_experiment_courses(
    term: str = Query(None, description="学年学期代码，如 2025-2026-2"),
    auth: NEUAuthClient = Depends(require_auth)
):
    """
    获取实验选课课程列表

    - term 不传则自动获取当前学期
    """
    try:
        print(f"[Experiment] 获取实验课程, term={term}")
        print(f"[Experiment] auth client: {auth}, username: {auth.username}")

        api = ExperimentCourseAPI(auth)

        # 如果没有传term，获取当前学期
        if not term:
            print("[Experiment] 未提供term，尝试自动获取...")
            term = api.get_semester()
            print(f"[Experiment] 自动获取学期: {term}")

        if not term:
            print("[Experiment] 无法获取学期，返回空列表")
            return {"courses": [], "term": "", "total": 0}

        print(f"[Experiment] 调用API获取课程，term={term}")
        courses = api.get_courses(term)
        print(f"[Experiment] 获取到 {len(courses)} 门课程")

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
        import traceback
        error_msg = f"获取实验课程失败: {e}"
        trace = traceback.format_exc()
        _api_logger.error(error_msg)
        _api_logger.error(trace)
        raise HTTPException(status_code=500, detail=error_msg)


@router.get("/experiment-courses/{task_id}/rounds")
async def get_experiment_rounds(
    task_id: str,
    course_no: str = Query(..., description="课程号"),
    project_code: str = Query(..., description="实验项目代码"),
    term: str = Query(..., description="学年学期代码"),
    auth: NEUAuthClient = Depends(require_auth)
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
        raise HTTPException(status_code=500, detail=f"获取实验班失败: {str(e)}")


@router.post("/experiment-courses/select")
async def select_experiment_course(
    data: Dict[str, str],
    auth: NEUAuthClient = Depends(require_auth)
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
        api = ExperimentCourseAPI(auth)

        result = api.select(
            data["term"],
            data["task_id"],
            data["project_code"],
            data["round_id"]
        )

        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"选课失败: {str(e)}")


@router.post("/experiment-courses/deselect")
async def deselect_experiment_course(
    data: Dict[str, str],
    auth: NEUAuthClient = Depends(require_auth)
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
        api = ExperimentCourseAPI(auth)

        result = api.deselect(
            data["term"],
            data["task_id"],
            data["project_code"],
            data["round_id"]
        )

        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"退课失败: {str(e)}")
