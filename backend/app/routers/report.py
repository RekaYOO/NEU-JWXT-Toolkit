from fastapi import APIRouter, HTTPException, Depends, Query

from backend.app.dependencies import get_storage
from backend.app.schemas import AcademicReportResponse
from backend.core.auth import NEUAuthClient
from backend.app.dependencies import (
    require_cached_auth_identity,
    require_serialized_auth,
)
from backend.app.cache_support import read_cache, submit_refresh, wait_for_job
from backend.core.log import log_application_error

router = APIRouter()


def _academic_report_response(entry, is_stale: bool, source: str = "local") -> AcademicReportResponse:
    report = entry.payload

    return AcademicReportResponse(
        student_name=report.get("student_name", ""),
        student_id=report.get("student_id", ""),
        grade=report.get("grade", ""),
        college=report.get("college", ""),
        major=report.get("major", ""),
        class_name=report.get("class_name", ""),
        expected_graduation=report.get("expected_graduation", ""),
        program_code=report.get("program_code", ""),
        program_name=report.get("program_name", ""),
        calculated_time=report.get("calculated_time", ""),
        credit_summary=report.get("credit_summary", {
            "total_required": 0,
            "total_passed": 0,
            "total_selected": 0,
            "total_earned": 0,
            "total_remaining": 0,
            "completion_rate": 0,
        }),
        categories=report.get("categories", []),
        outside_courses=report.get("outside_courses", []),
        source=source,
        is_fresh=not is_stale,
        last_update=entry.saved_at.isoformat(),
        cache=entry.metadata(is_stale=is_stale),
    )


@router.get("/academic-report/cache", response_model=AcademicReportResponse)
def get_cached_academic_report(
    auth: NEUAuthClient = Depends(require_cached_auth_identity)
):
    """Read the current account's report cache without contacting NEU."""
    entry, stale = read_cache(auth.username, "academic-report")
    if entry is None:
        raise HTTPException(status_code=404, detail="当前账号没有可用的本地培养计划缓存")
    return _academic_report_response(entry, stale)


@router.get("/academic-report", response_model=AcademicReportResponse)
def get_academic_report(
    refresh: bool = Query(False, description="强制刷新数据"),
    auth: NEUAuthClient = Depends(require_cached_auth_identity)
):
    """
    获取学业监测报告（培养计划）- 智能合并本地和远程数据

    - 默认优先使用本地缓存
    - 超过 5 分钟、成绩 revision 变化或 refresh=true 时提交统一后台刷新
    """
    try:
        entry, stale = read_cache(auth.username, "academic-report")
        submission = None
        if refresh or stale:
            submission = submit_refresh(
                auth.username,
                "academic-report",
                force=refresh,
                reason="manual" if refresh else "page_swr",
            )
        if entry is None or refresh:
            wait_for_job(submission.job_id if submission else None)
            entry, stale = read_cache(auth.username, "academic-report")
        if entry is None:
            raise HTTPException(status_code=503, detail="暂时无法获取培养计划且没有本地缓存")
        return _academic_report_response(entry, stale)
    except HTTPException:
        raise
    except Exception as e:
        error_id = log_application_error("academic_report.get", e, 500)
        raise HTTPException(status_code=500, detail=f"获取培养计划失败（错误编号：{error_id}）") from e


@router.post("/academic-report/refresh")
def refresh_academic_report(
    auth: NEUAuthClient = Depends(require_cached_auth_identity)
):
    """手动刷新培养计划数据"""
    submission = submit_refresh(
        auth.username, "academic-report", force=True, reason="manual"
    )
    job = wait_for_job(submission.job_id)
    if job and job.status.value == "completed":
        return {
            "success": True,
            "job_id": job.job_id,
            "revision": job.revision,
            "changed": job.changed,
            "diff": dict(job.changes),
        }
    raise HTTPException(
        status_code=503,
        detail=f"刷新失败: {getattr(job, 'error_kind', None) or 'unknown'}",
    )


@router.get("/academic-report/summary")
def get_academic_report_summary(
    refresh: bool = Query(False, description="强制刷新数据"),
    auth: NEUAuthClient = Depends(require_cached_auth_identity)
):
    """
    获取培养计划摘要信息（用于概览页面）
    """
    try:
        entry, stale = read_cache(auth.username, "academic-report")
        submission = None
        if refresh or stale:
            submission = submit_refresh(
                auth.username,
                "academic-report",
                force=refresh,
                reason="manual" if refresh else "page_swr",
            )
        if entry is None or refresh:
            wait_for_job(submission.job_id if submission else None)
            entry, stale = read_cache(auth.username, "academic-report")
        report = entry.payload if entry else None

        if report is None:
            raise RuntimeError("academic report payload unavailable")

        credit_summary = report.get("credit_summary", {})

        # 递归收集所有类别节点（包括子节点）
        def collect_categories(categories):
            result = []
            for cat in categories:
                cat_summary = {
                    "name": cat.get("name", ""),
                    "path": cat.get("path", ""),
                    "path_array": cat.get("path_array", []),
                    "is_leaf": cat.get("is_leaf", False),
                    "required_credits": cat.get("required_credits", 0),
                    "passed_credits": cat.get("passed_credits", 0),
                    "selected_credits": cat.get("selected_credits", 0),
                    "earned_credits": cat.get("earned_credits", 0),
                    "remaining_credits": cat.get("remaining_credits", 0),
                    "completion_rate": cat.get("completion_rate", 0),
                    "is_completed": cat.get("is_completed", False),
                    "course_count": len(cat.get("courses", [])),
                }
                result.append(cat_summary)
                # 递归收集子节点
                if cat.get("children"):
                    result.extend(collect_categories(cat.get("children", [])))
            return result

        return {
            "student_info": {
                "name": report.get("student_name", ""),
                "student_id": report.get("student_id", ""),
                "major": report.get("major", ""),
                "college": report.get("college", ""),
            },
            "program_info": {
                "name": report.get("program_name", ""),
                "code": report.get("program_code", ""),
            },
            "credit_summary": {
                "total_required": credit_summary.get("total_required", 0),
                "total_passed": credit_summary.get("total_passed", 0),
                "total_selected": credit_summary.get("total_selected", 0),
                "total_earned": credit_summary.get("total_earned", 0),
                "total_remaining": credit_summary.get("total_remaining", 0),
                "completion_rate": credit_summary.get("completion_rate", 0),
            },
            "category_summary": collect_categories(report.get("categories", [])),
            "calculated_time": report.get("calculated_time", ""),
            "source": "local",
            "is_fresh": not stale,
            "last_update": entry.saved_at.isoformat(),
            "cache": entry.metadata(is_stale=stale),
        }
    except HTTPException:
        raise
    except Exception as e:
        error_id = log_application_error("academic_report.summary", e, 500)
        raise HTTPException(status_code=500, detail=f"获取培养计划摘要失败（错误编号：{error_id}）") from e


@router.get("/academic-report/export")
def export_academic_report(
    auth: NEUAuthClient = Depends(require_serialized_auth)
):
    """
    导出培养计划为 CSV
    """
    try:
        report = auth.academic_report.get_report()
        if report is None:
            raise HTTPException(status_code=500, detail="获取培养计划失败")

        files = auth.academic_report.export_to_csv(
            report,
            output_dir=get_storage().config.data_dir,
        )

        return {
            "success": True,
            "message": "导出成功",
            "files": files
        }
    except Exception as e:
        error_id = log_application_error("academic_report.export", e, 500)
        raise HTTPException(status_code=500, detail=f"导出失败（错误编号：{error_id}）") from e
