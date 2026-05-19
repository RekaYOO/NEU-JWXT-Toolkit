from fastapi import APIRouter, HTTPException, Depends, Query

from backend.app.dependencies import _report_storage, _storage
from backend.app.schemas import AcademicReportResponse
from backend.core.auth import NEUAuthClient
from backend.app.dependencies import require_auth

router = APIRouter()


@router.get("/academic-report", response_model=AcademicReportResponse)
async def get_academic_report(
    refresh: bool = Query(False, description="强制刷新数据"),
    auth: NEUAuthClient = Depends(require_auth)
):
    """
    获取学业监测报告（培养计划）- 智能合并本地和远程数据

    - 默认优先使用本地缓存
    - 仅当成绩更新或 refresh=true 时拉取云端
    """
    try:
        result = _report_storage.get_report_smart(auth, force_refresh=refresh)
        report = result["report"]

        # 格式化 last_update 为 ISO 字符串
        last_update = result.get("last_update")
        if last_update and hasattr(last_update, 'isoformat'):
            last_update = last_update.isoformat()

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
            source=result.get("source", "remote"),
            is_fresh=result.get("is_fresh", True),
            last_update=last_update
        )
    except Exception as e:
        import traceback
        print(f"获取培养计划错误: {e}")
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"获取培养计划失败: {str(e)}")


@router.post("/academic-report/refresh")
async def refresh_academic_report(auth: NEUAuthClient = Depends(require_auth)):
    """手动刷新培养计划数据"""
    result = _report_storage.refresh_report(auth)

    if result["success"]:
        return result
    else:
        raise HTTPException(status_code=500, detail=result["message"])


@router.get("/academic-report/summary")
async def get_academic_report_summary(
    refresh: bool = Query(False, description="强制刷新数据"),
    auth: NEUAuthClient = Depends(require_auth)
):
    """
    获取培养计划摘要信息（用于概览页面）
    """
    try:
        result = _report_storage.get_report_smart(auth, force_refresh=refresh)
        report = result["report"]

        if report is None:
            raise HTTPException(status_code=500, detail="获取培养计划失败")

        credit_summary = report.get("credit_summary", {})

        # 格式化 last_update 为 ISO 字符串
        last_update = result.get("last_update")
        if last_update and hasattr(last_update, 'isoformat'):
            last_update = last_update.isoformat()

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
            "source": result.get("source", "remote"),
            "is_fresh": result.get("is_fresh", True),
            "last_update": last_update,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取培养计划摘要失败: {str(e)}")


@router.get("/academic-report/export")
async def export_academic_report(auth: NEUAuthClient = Depends(require_auth)):
    """
    导出培养计划为 CSV
    """
    try:
        report = auth.academic_report.get_report()
        if report is None:
            raise HTTPException(status_code=500, detail="获取培养计划失败")

        files = auth.academic_report.export_to_csv(report, output_dir=_storage.config.data_dir)

        return {
            "success": True,
            "message": "导出成功",
            "files": files
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"导出失败: {str(e)}")
