"""Local, deterministic scheduling services shared across feature pages."""

from fastapi import APIRouter, Depends, HTTPException

from backend.app.dependencies import (
    get_cache_coordinator,
    remote_session_guard,
    require_cached_auth_identity,
)
from backend.app.schemas.scheduling import (
    ScheduleCandidateConflictModel,
    ScheduleConflictBatchRequest,
    ScheduleConflictBatchResponse,
    ScheduleConflictMatchModel,
)
from backend.core.scheduling import check_conflicts, normalize_meeting
from backend.core.cache.resources import personal_timetable_variant
from backend.core.auth import NEUAuthClient
from backend.core.auth.client import NEULoginError
from backend.core.timetable import TimetableError
from backend.core.scheduling.models import (
    CandidateConflictResult,
    ConflictStatus,
)


router = APIRouter(prefix="/schedule", tags=["schedule"])


@router.post("/conflicts/check", response_model=ScheduleConflictBatchResponse)
def check_schedule_conflicts(
    request: ScheduleConflictBatchRequest,
    auth: NEUAuthClient = Depends(require_cached_auth_identity),
) -> ScheduleConflictBatchResponse:
    """Compare candidates with this account's personal timetable."""

    entry, baseline_stale = get_cache_coordinator().read(
        account_id=str(auth.username),
        resource="personal-timetable",
        variant=personal_timetable_variant(request.term_code),
    )
    payload = entry.payload if entry is not None and isinstance(entry.payload, dict) else None
    baseline_available = bool(
        payload is not None
        and str(payload.get("term_code") or "") == request.term_code
        and isinstance(payload.get("courses"), list)
    )
    baseline = (
        [
            normalize_meeting(
                row,
                term_code=request.term_code,
                default_week=request.week,
                default_source="personal_timetable",
            )
            for row in payload.get("courses", [])
            if isinstance(row, dict)
        ]
        if baseline_available
        else []
    )
    used_live_baseline = False
    if request.resolve_personal_timetable:
        try:
            with remote_session_guard():
                live_payload = auth.timetable.get_schedule(
                    mode="personal",
                    term_code=request.term_code,
                    campus_code="all",
                    week=None,
                )
            live_courses = live_payload.get("courses") if isinstance(live_payload, dict) else None
            if isinstance(live_courses, list):
                baseline = [
                    normalize_meeting(
                        row,
                        term_code=request.term_code,
                        default_source="personal_timetable",
                    )
                    for row in live_courses
                    if isinstance(row, dict)
                ]
                baseline_available = True
                baseline_stale = False
                used_live_baseline = True
        except NEULoginError as error:
            raise HTTPException(status_code=401, detail="统一认证会话已过期") from error
        except TimetableError:
            # Interactive timetable preview checks must never show conflicts
            # from an older cached schedule when the authoritative same-term
            # personal timetable cannot be read.  Existing non-interactive
            # callers keep the original cache-only behavior because they do
            # not set resolve_personal_timetable.
            baseline = []
            baseline_available = False
            baseline_stale = True
    candidates = [
        normalize_meeting(
            row.model_dump(),
            term_code=request.term_code,
            default_week=request.week,
            default_source="candidate",
        )
        for row in request.candidates
    ]
    results = (
        check_conflicts(
            baseline,
            candidates,
            ignore_same_course=request.ignore_same_course,
        )
        if baseline_available
        else tuple(
            CandidateConflictResult(
                candidate_id=candidate.source_id or candidate.meeting_id,
                candidate_meeting_id=candidate.meeting_id,
                status=ConflictStatus.UNKNOWN,
            )
            for candidate in candidates
        )
    )
    if baseline_available and baseline_stale:
        results = tuple(
            CandidateConflictResult(
                candidate_id=result.candidate_id,
                candidate_meeting_id=result.candidate_meeting_id,
                status=ConflictStatus.UNKNOWN,
                matches=result.matches,
            )
            for result in results
        )
    return ScheduleConflictBatchResponse(
        term_code=request.term_code,
        week=request.week,
        baseline_count=len(baseline),
        baseline_available=baseline_available,
        baseline_revision=entry.revision if baseline_available and not used_live_baseline else None,
        baseline_stale=bool(baseline_stale or not baseline_available),
        candidate_count=len(candidates),
        results=[
            ScheduleCandidateConflictModel(
                candidate_id=result.candidate_id,
                candidate_meeting_id=result.candidate_meeting_id,
                status=result.status.value,
                matches=[
                    ScheduleConflictMatchModel(
                        baseline_meeting_id=match.baseline_meeting_id,
                        baseline_course_name=match.baseline_course_name,
                        baseline_course_code=match.baseline_course_code,
                        baseline_teaching_class_id=match.baseline_teaching_class_id,
                        baseline_weeks=list(match.baseline_weeks),
                        status=match.status.value,
                        reason=match.reason,
                        overlapping_weeks=list(match.overlapping_weeks),
                        weekday=match.weekday,
                        start_section=match.start_section,
                        end_section=match.end_section,
                    )
                    for match in result.matches
                ],
            )
            for result in results
        ],
    )
