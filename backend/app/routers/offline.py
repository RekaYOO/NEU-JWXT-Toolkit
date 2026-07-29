"""Read-only access to locally cached academic data without a NEU session."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from backend.app.dependencies import (
    _cache_coordinator,
    _cache_registry,
    _cache_store,
    _report_storage,
    _storage,
)
from backend.app.schemas import AcademicReportResponse, CourseScoreModel, ScoresResponse
from backend.app.routers.scores import _score_model
from backend.app.cache_support import read_cache_offline


router = APIRouter()


def _offline_account() -> str | None:
    account = _cache_store.latest_account_for(("scores", "academic-report"))
    if account:
        return account
    score_data = _storage.load_scores_with_meta()
    report_data = _report_storage.load_report() or {}
    legacy_account = (
        (score_data.get("meta") or {}).get("username")
        or report_data.get("username")
        or ""
    )
    return str(legacy_account) or None


def _offline_status() -> dict:
    account = _offline_account()
    if account:
        score_entry, _ = read_cache_offline(account, "scores")
        report_entry, _ = read_cache_offline(account, "academic-report")
        has_scores = _compatible(score_entry, "scores")
        has_report = _compatible(report_entry, "academic-report")
        return {
            "available": has_scores or has_report,
            "has_scores": has_scores,
            "has_report": has_report,
            "username": account,
            "read_only": True,
        }
    score_data = _storage.load_scores_with_meta()
    report_data = _report_storage.load_report() or {}
    has_scores = bool(score_data.get("scores"))
    has_report = bool(report_data.get("report"))
    username = (
        (score_data.get("meta") or {}).get("username")
        or report_data.get("username")
        or ""
    )
    return {
        "available": has_scores or has_report,
        "has_scores": has_scores,
        "has_report": has_report,
        "username": str(username),
        "read_only": True,
    }


def _compatible(entry, resource: str) -> bool:
    if entry is None:
        return False
    spec = _cache_registry.get(resource)
    return (
        entry.schema_version == spec.schema_version
        and entry.revision_algorithm_version == spec.revision_algorithm_version
        and entry.payload_type == spec.payload_type
        and spec.offline_readable
    )


def _offline_entry(resource: str):
    account = _offline_account()
    if not account:
        return None, None
    entry, stale = read_cache_offline(account, resource)
    return (entry, stale) if _compatible(entry, resource) else (None, None)


@router.get("/status")
def offline_status():
    return _offline_status()


@router.get("/scores", response_model=ScoresResponse)
def offline_scores():
    entry, stale = _offline_entry("scores")
    if entry:
        scores = entry.payload.get("scores") or []
        models = [_score_model(score) for score in scores]
        credits = sum(model.credit for model in models)
        return ScoresResponse(
            total_courses=len(models),
            overall_gpa=entry.payload.get("overall_gpa"),
            calculated_gpa=(
                sum(model.gpa * model.credit for model in models) / credits
                if credits else 0
            ),
            source="offline",
            is_fresh=False,
            last_update=entry.saved_at,
            cache=entry.metadata(is_stale=bool(stale)),
            scores=models,
        )
    local = _storage.load_scores_with_meta()
    scores = local.get("scores") or []
    if not scores:
        raise HTTPException(status_code=404, detail="本地没有已保存的成绩数据")

    score_models = [
        CourseScoreModel(
            name=score.name,
            code=score.code,
            score=score.score,
            score_value=score.get_score_value(),
            gpa=score.gpa,
            credit=score.credit,
            term=score.term,
            term_display=score.term_display,
            course_type=score.course_type,
            course_category=score.course_category,
            general_category=score.general_category,
            exam_type=score.exam_type,
            exam_status=score.exam_status,
            course_nature=score.course_nature,
            is_passed=score.is_passed,
        )
        for score in scores
    ]
    total_credits = sum(score.credit for score in scores)
    calculated_gpa = (
        sum(score.gpa * score.credit for score in scores) / total_credits
        if total_credits > 0
        else 0.0
    )
    return ScoresResponse(
        total_courses=len(scores),
        overall_gpa=(local.get("meta") or {}).get("overall_gpa"),
        calculated_gpa=calculated_gpa,
        source="offline",
        is_fresh=False,
        last_update=_storage.get_last_update_time(),
        scores=score_models,
    )


@router.get("/academic-report", response_model=AcademicReportResponse)
def offline_academic_report():
    entry, stale = _offline_entry("academic-report")
    if entry:
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
            credit_summary=report.get("credit_summary", {}),
            categories=report.get("categories", []),
            outside_courses=report.get("outside_courses", []),
            source="offline",
            is_fresh=False,
            last_update=entry.saved_at.isoformat(),
            cache=entry.metadata(is_stale=bool(stale)),
        )
    local = _report_storage.load_report() or {}
    report = local.get("report")
    if not report:
        raise HTTPException(status_code=404, detail="本地没有已保存的培养计划数据")

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
        source="offline",
        is_fresh=False,
        last_update=local.get("saved_at"),
    )
