from typing import List
from fastapi import APIRouter, HTTPException, Depends, Query

from backend.app.dependencies import _academic_storage
from backend.app.schemas import CourseScoreModel, TermScoresModel, ScoresResponse, ColumnConfig
from backend.core.auth import NEUAuthClient
from backend.app.dependencies import require_auth

router = APIRouter()


@router.get("/scores", response_model=ScoresResponse)
async def get_scores(
    refresh: bool = Query(False, description="强制刷新数据"),
    auth: NEUAuthClient = Depends(require_auth)
):
    """
    获取成绩列表 - 智能合并本地和远程数据

    - 默认使用本地缓存
    - 缓存过期(3天)或refresh=true时自动拉取云端
    """
    try:
        result = _academic_storage.get_scores_smart(auth, force_refresh=refresh)
        scores = result["scores"]

        # 转换为模型
        score_models = [
            CourseScoreModel(
                name=s.name,
                code=s.code,
                score=s.score,
                score_value=s.get_score_value(),
                gpa=s.gpa,
                credit=s.credit,
                term=s.term,
                term_display=s.term_display,
                course_type=s.course_type,
                course_category=s.course_category,
                general_category=s.general_category,
                exam_type=s.exam_type,
                exam_status=s.exam_status,
                course_nature=s.course_nature,
                is_passed=s.is_passed
            )
            for s in scores
        ]

        return ScoresResponse(
            total_courses=len(scores),
            overall_gpa=result.get("overall_gpa"),
            calculated_gpa=sum(s.gpa * s.credit for s in scores) / sum(s.credit for s in scores) if scores else 0.0,
            source=result["source"],
            is_fresh=result["is_fresh"],
            last_update=result.get("last_update"),
            scores=score_models
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取成绩失败: {str(e)}")


@router.get("/scores/by-term", response_model=List[TermScoresModel])
async def get_scores_by_term(auth: NEUAuthClient = Depends(require_auth)):
    """按学期获取成绩"""
    try:
        term_scores = auth.academic.get_scores_by_term()

        result = []
        for ts in term_scores:
            courses = [
                CourseScoreModel(
                    name=c.name,
                    code=c.code,
                    score=c.score,
                    score_value=c.get_score_value(),
                    gpa=c.gpa,
                    credit=c.credit,
                    term=c.term,
                    term_display=c.term_display,
                    course_type=c.course_type,
                    course_category=c.course_category,
                    general_category=c.general_category,
                    exam_type=c.exam_type,
                    exam_status=c.exam_status,
                    course_nature=c.course_nature,
                    is_passed=c.is_passed
                )
                for c in ts.courses
            ]

            result.append(TermScoresModel(
                term_code=ts.term_code,
                term_name=ts.term_name,
                courses=courses,
                total_credits=ts.total_credits,
                gpa=ts.gpa
            ))

        return result

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取成绩失败: {str(e)}")


@router.post("/scores/refresh")
async def refresh_scores(auth: NEUAuthClient = Depends(require_auth)):
    """手动刷新成绩数据"""
    result = _academic_storage.refresh_scores(auth)

    if result["success"]:
        return result
    else:
        raise HTTPException(status_code=500, detail=result["message"])


@router.get("/columns/default")
async def get_default_columns() -> List[ColumnConfig]:
    """获取默认列配置"""
    return [
        ColumnConfig(key="name", title="课程名称", visible=True, width=200),
        ColumnConfig(key="code", title="课程代码", visible=True, width=120),
        ColumnConfig(key="score", title="成绩", visible=True, width=80),
        ColumnConfig(key="gpa", title="绩点", visible=True, width=80),
        ColumnConfig(key="credit", title="学分", visible=True, width=80),
        ColumnConfig(key="term_display", title="学期", visible=True, width=180),
        ColumnConfig(key="course_type", title="课程性质", visible=True, width=100),
        ColumnConfig(key="course_category", title="课程类别", visible=False, width=150),
        ColumnConfig(key="general_category", title="通识类别", visible=False, width=150),
        ColumnConfig(key="exam_type", title="考核方式", visible=False, width=100),
        ColumnConfig(key="exam_status", title="考试状态", visible=False, width=100),
        ColumnConfig(key="is_passed", title="状态", visible=True, width=80),
    ]
