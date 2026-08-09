from dataclasses import asdict

from fastapi import APIRouter, HTTPException, Depends, Query

from backend.app.dependencies import get_auth_generation, get_cache_coordinator
from backend.core.auth import NEUAuthClient
from backend.core.academic.experiment import ExperimentCourseAPI
from backend.core.cache import mutation_policy
from backend.core.cache.resources import personal_timetable_variant
from backend.core.scheduling import check_conflicts, normalize_meeting
from backend.core.log import log_application_error
from backend.app.dependencies import require_serialized_auth
from backend.app.schemas import ExperimentCourseMutationRequest

router = APIRouter()


def _best_effort_current_term(auth: NEUAuthClient) -> str:
    try:
        return next(
            (
                str(item.get("code") or "")
                for item in auth.timetable.get_terms()
                if item.get("current")
            ),
            "",
        )
    except Exception:
        # A successful remote write must not be turned into a reported failure
        # merely because the follow-up cache routing check is unavailable.
        return ""


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
        coordinator = get_cache_coordinator()
        entry, baseline_stale = coordinator.read(
            account_id=str(auth.username),
            resource="personal-timetable",
            variant=personal_timetable_variant(term),
        )
        baseline_payload = (
            entry.payload
            if entry is not None
            and isinstance(entry.payload, dict)
            and str(entry.payload.get("term_code") or "") == term
            else None
        )
        baseline = [
            normalize_meeting(
                row,
                term_code=term,
                default_source="personal_timetable",
            )
            for row in (baseline_payload or {}).get("courses", [])
            if isinstance(row, dict)
        ]
        candidates = [
            normalize_meeting(
                {
                    **asdict(round_),
                    "candidate_id": round_.wid,
                    "course_name": round_.round_name or project_code,
                    "course_code": course_no,
                    "activity_type": "experiment",
                },
                term_code=term,
                default_source="experiment",
            )
            for round_ in rounds
        ]
        local_results = (
            {result.candidate_id: result for result in check_conflicts(baseline, candidates)}
            if baseline_payload is not None
            else {}
        )
        candidate_by_id = {
            candidate.source_id or candidate.meeting_id: candidate
            for candidate in candidates
        }

        def round_model(round_):
            local = local_results.get(round_.wid)
            candidate = candidate_by_id.get(round_.wid)
            local_status = local.status.value if local is not None else "unknown"
            if baseline_stale and local_status in {"clear", "conflict"}:
                local_status = "unknown"
            combined_status = "conflict" if round_.conflict else local_status
            window_state = round_.selection_window_state()
            can_select = bool(
                round_.can_select
                and combined_status != "conflict"
            )
            disabled_reason = ""
            if round_.is_full:
                disabled_reason = "实验班已满"
            elif round_.conflict:
                disabled_reason = "教务系统判定时间冲突"
            elif local_status == "conflict":
                disabled_reason = "与个人课表冲突"
            elif window_state == "not_started":
                disabled_reason = "尚未到选课时间"
            elif window_state == "ended":
                disabled_reason = "选课时间已结束"
            return {
                "wid": round_.wid,
                "round_name": round_.round_name,
                "teacher": round_.teacher,
                "selected_count": round_.selected_count,
                "capacity": round_.capacity,
                "is_full": round_.is_full,
                "week": round_.week,
                "day": round_.day,
                "time": round_.time,
                "location": round_.location,
                "select_start": round_.select_start,
                "select_end": round_.select_end,
                "selection_window_state": window_state,
                "conflict": round_.conflict,
                "conflict_status": combined_status,
                "conflict_source": "official" if round_.conflict else "personal_timetable",
                "conflicts": [
                    {
                        "meeting_id": match.baseline_meeting_id,
                        "course_name": match.baseline_course_name,
                        "reason": match.reason,
                        "overlapping_weeks": list(match.overlapping_weeks),
                        "weekday": match.weekday,
                        "start_section": match.start_section,
                        "end_section": match.end_section,
                    }
                    for match in (local.matches if local is not None else ())
                ],
                "baseline_available": baseline_payload is not None,
                "baseline_stale": bool(baseline_stale or baseline_payload is None),
                "selected": round_.selected,
                "can_select": can_select,
                "disabled_reason": disabled_reason,
                "weeks": list(candidate.weeks) if candidate is not None else [],
                "weekday": candidate.weekday if candidate is not None else 0,
                "start_section": candidate.start_section if candidate is not None else 0,
                "end_section": candidate.end_section if candidate is not None else 0,
            }

        return {
            "rounds": [round_model(round_) for round_ in rounds],
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
            current_term = _best_effort_current_term(auth)
            coordinator = get_cache_coordinator()
            for resource in policy.invalidations:
                if resource in coordinator.registry.resources():
                    options = {
                        "account_id": str(auth.username),
                        "resource": resource,
                    }
                    if resource == "personal-timetable":
                        options["variant"] = personal_timetable_variant(data.term)
                    coordinator.invalidate(**options)
            for resource in policy.refetches:
                if resource in coordinator.registry.resources():
                    if resource == "personal-timetable" and data.term != current_term:
                        continue
                    options = {
                        "account_id": str(auth.username),
                        "resource": resource,
                        "identity_epoch": get_auth_generation(),
                        "force": True,
                        "reason": "foreground_mutation",
                    }
                    if resource == "personal-timetable":
                        options["variant"] = personal_timetable_variant(data.term)
                    try:
                        coordinator.submit(**options)
                    except RuntimeError:
                        pass

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
            current_term = _best_effort_current_term(auth)
            coordinator = get_cache_coordinator()
            for resource in policy.invalidations:
                if resource in coordinator.registry.resources():
                    options = {
                        "account_id": str(auth.username),
                        "resource": resource,
                    }
                    if resource == "personal-timetable":
                        options["variant"] = personal_timetable_variant(data.term)
                    coordinator.invalidate(**options)
            for resource in policy.refetches:
                if resource in coordinator.registry.resources():
                    if resource == "personal-timetable" and data.term != current_term:
                        continue
                    options = {
                        "account_id": str(auth.username),
                        "resource": resource,
                        "identity_epoch": get_auth_generation(),
                        "force": True,
                        "reason": "foreground_mutation",
                    }
                    if resource == "personal-timetable":
                        options["variant"] = personal_timetable_variant(data.term)
                    try:
                        coordinator.submit(**options)
                    except RuntimeError:
                        pass

        return result
    except Exception as e:
        error_id = log_application_error("experiment.deselect", e, 500)
        raise HTTPException(status_code=500, detail=f"退课失败（错误编号：{error_id}）") from e
