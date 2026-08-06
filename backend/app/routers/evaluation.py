from typing import Optional, List, Dict, Any
import time as py_time
from fastapi import APIRouter, HTTPException, Depends, Query

from backend.app.dependencies import get_api_logger
from backend.app.schemas import EvaluationSubmitRequest, EvaluationBatchRequest
from backend.core.auth import NEUAuthClient
from backend.core.cache import mutation_policy
from backend.core.evaluation.api import EvaluationAPI
from backend.core.log import log_application_error
from backend.mock_evaluation import (
    EVAL_TEST_MODE, get_mock_tasks, get_mock_courses,
    get_mock_indicators, mock_submit, mock_batch,
)
from backend.app.dependencies import require_serialized_auth

router = APIRouter()

_IDENTIFIER_PATTERN = r"^[^\x00-\x1f\x7f]+$"
_MAX_BATCH_COURSES = 50


@router.get("/evaluation/cycles")
def get_evaluation_cycles(
    auth: NEUAuthClient = Depends(require_serialized_auth)
):
    """
    获取所有可用学年学期

    从评教系统 findCycle 接口获取，isdefault=1 的为当前默认学期。
    前端可用于渲染学期选择器。
    """
    try:
        api = EvaluationAPI(auth)

        cycles = api.get_cycles()
        default_value = next((c["value"] for c in cycles if c.get("isdefault") == 1), cycles[0]["value"] if cycles else "")

        return {
            "cycles": cycles,
            "default": default_value,
        }
    except Exception as e:
        error_id = log_application_error("evaluation.list_cycles", e, 500)
        raise HTTPException(status_code=500, detail=f"获取学期列表失败（错误编号：{error_id}）") from e


@router.get("/evaluation/tasks")
def get_evaluation_tasks(
    xnxq: Optional[str] = Query(
        None,
        min_length=1,
        max_length=32,
        pattern=_IDENTIFIER_PATTERN,
        description="学年学期，不传则使用系统默认",
    ),
    auth: NEUAuthClient = Depends(require_serialized_auth)
):
    """
    获取学生评教任务列表（一级页面）

    返回所有评教任务，每个任务包含待评/已评课程数
    """
    if EVAL_TEST_MODE:
        return get_mock_tasks()

    try:
        api = EvaluationAPI(auth)

        # 不传 xnxq 时自动获取默认学期
        if not xnxq:
            xnxq = api.get_default_cycle()

        tasks = api.get_tasks(xnxq)

        return {
            "tasks": [
                {
                    "task_id": t.task_id,
                    "task_name": t.task_name,
                    "total_count": t.total_count,
                    "evaluated_count": t.evaluated_count,
                    "pending_count": t.pending_count,
                    "status": t.status,
                }
                for t in tasks
            ],
            "total": len(tasks),
            "xnxq": xnxq,
        }
    except Exception as e:
        error_id = log_application_error("evaluation.list_tasks", e, 500)
        raise HTTPException(status_code=500, detail=f"获取评教任务失败（错误编号：{error_id}）") from e


@router.get("/evaluation/tasks/{task_id}/courses")
def get_evaluation_courses(
    task_id: str,
    xnxq: Optional[str] = Query(
        None,
        min_length=1,
        max_length=32,
        pattern=_IDENTIFIER_PATTERN,
        description="学年学期，不传则使用系统默认",
    ),
    auth: NEUAuthClient = Depends(require_serialized_auth)
):
    """
    获取评教任务下的课程列表（二级页面）

    返回指定任务下所有待评价和已评价的课程
    """
    if EVAL_TEST_MODE:
        return get_mock_courses(task_id)

    try:
        api = EvaluationAPI(auth)

        # 不传 xnxq 时自动获取默认学期
        if not xnxq:
            xnxq = api.get_default_cycle()

        courses = api.get_courses(task_id, xnxq)

        return {
            "task_id": task_id,
            "courses": [
                {
                    "xspjid": c.xspjid,
                    "task_id": c.task_id,
                    "task_name": c.task_name,
                    "course_name": c.course_name,
                    "teacher_name": c.teacher_name,
                    "teacher_code": c.teacher_code,
                    "department": c.department,
                    "department_id": c.department_id,
                    "course_type_name": c.course_type_name,
                    "is_submit": c.is_submit,
                    "is_evaluated": c.is_evaluated,
                    "is_kpj": c.is_kpj,
                    "score": c.score,
                    "avg_score": c.avg_score,
                }
                for c in courses
            ],
            "total": len(courses),
            "pending": len([c for c in courses if not c.is_evaluated]),
            "completed": len([c for c in courses if c.is_evaluated]),
        }
    except Exception as e:
        error_id = log_application_error("evaluation.list_courses", e, 500)
        raise HTTPException(status_code=500, detail=f"获取评教课程列表失败（错误编号：{error_id}）") from e


@router.get("/evaluation/courses/{xspjid}/indicators")
def get_evaluation_indicators(
    xspjid: str,
    task_id: str = Query(..., description="评教任务ID"),
    auth: NEUAuthClient = Depends(require_serialized_auth)
):
    """
    获取课程的评教指标体系

    返回指定课程的评分指标（评分细则）
    """
    if EVAL_TEST_MODE:
        return get_mock_indicators(xspjid, task_id)

    try:
        api = EvaluationAPI(auth)

        # 从课程列表获取 xnxqid
        courses = api.get_courses(task_id)
        course_obj = next((c for c in courses if c.xspjid == xspjid), None)
        xnxqid = course_obj.xnxqid if course_obj else ""

        target = api.get_evaluation_target(task_id, xspjid, xnxqid)
        if not target:
            raise HTTPException(status_code=404, detail="获取评教指标体系失败")

        # 计算已评分数（如果指标中有 dfdj）
        score_map = {6: 100, 5: 90, 4: 80, 3: 70, 2: 60, 1: 50}
        scored_indicators = []
        total_eval_score = 0
        scored_count = 0
        for ind in target.indicators:
            item = {
                "zbid": ind.zbid,
                "zbmc": ind.zbmc,
                "evaltype": ind.evaltype,
                "sfdx": ind.sfdx,
                "sfbt": ind.sfbt,
                "weight": ind.weight,
                "fz": ind.fz,
                "jsjx": ind.jsjx,
                "sort": ind.sort,
                "level_json": ind.level_json,
                "parent_id": ind.parent_id,
                "dfdj": ind.dfdj,
                "result": ind.result,
            }
            # 如果已评，计算对应分数
            if ind.dfdj is not None:
                if isinstance(ind.dfdj, list):
                    item["score"] = [score_map.get(d, 0) for d in ind.dfdj]
                    total_eval_score += max(item["score"])
                else:
                    item["score"] = score_map.get(ind.dfdj, 0)
                    total_eval_score += item["score"]
                scored_count += 1
            else:
                item["score"] = None
            scored_indicators.append(item)

        avg_score = round(total_eval_score / scored_count, 1) if scored_count > 0 else None

        return {
            "libid": target.libid,
            "libname": target.libname,
            "preface": target.preface,
            "total_score": target.total_score,
            "indicators": scored_indicators,
            "total_indicators": len(target.indicators),
            "selection_count": len([i for i in target.indicators if i.evaltype == 1]),
            "text_count": len([i for i in target.indicators if i.evaltype != 1]),
            "avg_score": avg_score,
            "scored_count": scored_count,
        }
    except HTTPException:
        raise
    except Exception as e:
        error_id = log_application_error("evaluation.get_indicators", e, 500)
        raise HTTPException(status_code=500, detail=f"获取评教指标失败（错误编号：{error_id}）") from e


@router.post("/evaluation/submit")
def submit_evaluation(
    request: EvaluationSubmitRequest,
    auth: NEUAuthClient = Depends(require_serialized_auth)
):
    """
    提交评教结果

    ⚠️ 评教系统不支持重试！
    """
    policy = mutation_policy("evaluation.submit")
    if EVAL_TEST_MODE:
        result = mock_submit(request)
        return {
            **result,
            "refetches": (
                policy.refetches
                if result.get("success") and not request.dry_run
                else ()
            ),
        }

    try:
        api = EvaluationAPI(auth)

        # 获取课程信息
        courses = api.get_courses(request.task_id)
        course = None
        for c in courses:
            if c.xspjid == request.xspjid:
                course = c
                break

        if not course:
            raise HTTPException(status_code=404, detail=f"未找到课程: xspjid={request.xspjid}")

        # 获取指标体系
        target = api.get_evaluation_target(request.task_id, request.xspjid, course.xnxqid)
        if not target:
            raise RuntimeError("evaluation target unavailable")

        # 应用文本型指标内容
        if request.text_results:
            for ind in target.indicators:
                if ind.zbid in request.text_results:
                    ind.result = request.text_results[ind.zbid]

        result = (
            api.preview_evaluation(
                course,
                target,
                request.strategy,
                request.custom_scores,
            )
            if request.dry_run
            else api.submit_evaluation(
                course,
                target,
                request.strategy,
                request.custom_scores,
            )
        )

        if result["success"]:
            get_api_logger().info(
                "Evaluation preview completed"
                if request.dry_run
                else "Evaluation submission completed"
            )
        else:
            get_api_logger().warning("Evaluation submission was rejected")

        return {
            **result,
            "course_name": course.course_name,
            "teacher_name": course.teacher_name,
            "refetches": (
                policy.refetches
                if result["success"] and not request.dry_run
                else ()
            ),
        }
    except HTTPException:
        raise
    except Exception as e:
        error_id = log_application_error("evaluation.submit", e, 500)
        raise HTTPException(status_code=500, detail=f"提交评教失败（错误编号：{error_id}）") from e


@router.post("/evaluation/batch")
def batch_evaluation(
    request: EvaluationBatchRequest,
    auth: NEUAuthClient = Depends(require_serialized_auth)
):
    """
    批量评教

    对指定任务下选中的未评课程应用相同策略进行评教。
    若未提供 xspjids，则默认提交全部待评课程。
    """
    policy = mutation_policy("evaluation.batch")
    if EVAL_TEST_MODE:
        result = mock_batch(request)
        return {
            **result,
            "refetches": (
                policy.refetches
                if result.get("success_count") and not request.dry_run
                else ()
            ),
        }

    try:
        api = EvaluationAPI(auth)

        # 获取课程列表
        courses = api.get_courses(request.task_id)
        pending = [c for c in courses if not c.is_evaluated]

        # 根据前端传入的 xspjids 过滤选中课程
        if request.xspjids:
            pending = [c for c in pending if c.xspjid in request.xspjids]

        if len(pending) > _MAX_BATCH_COURSES:
            raise HTTPException(
                status_code=400,
                detail=f"Batch evaluation is limited to {_MAX_BATCH_COURSES} courses",
            )

        if not pending:
            return {
                "results": [],
                "total": 0,
                "pending_count": 0,
                "success_count": 0,
                "message": "没有待评课程",
            }

        # 实际批量提交：只对选中的 pending 课程逐条提交
        results = []
        for i, course in enumerate(pending):
            if request.dry_run:
                target = api.get_evaluation_target(
                    request.task_id,
                    course.xspjid,
                    course.xnxqid,
                )
                result = (
                    api.preview_evaluation(
                        course,
                        target,
                        request.strategy,
                        request.custom_scores,
                    )
                    if target
                    else {
                        "success": False,
                        "dry_run": True,
                        "message": "评教指标不可用",
                    }
                )
            else:
                result = api.evaluate_course(
                    course,
                    request.strategy,
                    request.custom_scores,
                )
            results.append({
                "course_name": course.course_name,
                "teacher_name": course.teacher_name,
                **result,
            })
            if i < len(pending) - 1:
                py_time.sleep(request.delay)

        success_count = sum(1 for r in results if r["success"])
        get_api_logger().info(
            "Evaluation batch completed: %s/%s succeeded",
            success_count,
            len(results),
        )

        return {
            "results": results,
            "total": len(results),
            "pending_count": len(pending),
            "success_count": success_count,
            "dry_run": request.dry_run,
            "refetches": (
                policy.refetches
                if success_count and not request.dry_run
                else ()
            ),
        }
    except HTTPException:
        raise
    except Exception as e:
        error_id = log_application_error("evaluation.batch", e, 500)
        raise HTTPException(status_code=500, detail=f"批量评教失败（错误编号：{error_id}）") from e
