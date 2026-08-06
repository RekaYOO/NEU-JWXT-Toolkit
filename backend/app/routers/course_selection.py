"""Stateless API for the new course-weight game model."""

from __future__ import annotations

import logging
from threading import BoundedSemaphore
import time

from fastapi import APIRouter, HTTPException, Response

from backend.app.schemas.course_selection import (
    CourseSelectionOptimizeRequest,
    CourseSelectionOptimizeResponse,
)
from backend.core.course_selection import (
    CourseMarket,
    CourseSelectionError,
    MarketSnapshot,
    SelectionPolicy,
    optimize_course_weights,
)
from backend.core.course_selection.model import TieRule
from backend.core.log import log_application_error


router = APIRouter(prefix="/course-selection", tags=["course-selection"])
logger = logging.getLogger(__name__)
_solver_slots = BoundedSemaphore(value=2)


@router.post("/optimize", response_model=CourseSelectionOptimizeResponse)
def optimize_course_selection(
    request: CourseSelectionOptimizeRequest,
    response: Response,
) -> CourseSelectionOptimizeResponse:
    """Calculate model strategies without reading or persisting user data."""

    started_at = time.perf_counter()
    response.headers["Cache-Control"] = "no-store"
    try:
        policy = SelectionPolicy(
            budget=request.policy.budget,
            min_bid=request.policy.min_bid,
            bid_step=request.policy.bid_step,
            tie_rule=TieRule(request.policy.tie_rule),
            max_selected_courses=request.policy.max_selected_courses,
            demand_multipliers=request.policy.demand_multipliers,
        )
        market = MarketSnapshot(
            cohort_size=request.market.cohort_size,
            captured_at=request.market.captured_at,
            is_complete=request.market.is_complete,
            courses=tuple(
                CourseMarket(
                    course_id=course.course_id,
                    name=course.name,
                    capacity=course.capacity,
                    current_participants=course.current_participants,
                    target_included=course.target_included,
                    target_interested=course.target_interested,
                    target_utility=course.target_utility,
                )
                for course in request.market.courses
            ),
        )
        if not _solver_slots.acquire(blocking=False):
            raise HTTPException(status_code=429, detail="模型计算繁忙，请稍后重试")
        try:
            result = optimize_course_weights(policy, market)
        finally:
            _solver_slots.release()
    except HTTPException:
        raise
    except CourseSelectionError as exc:
        logger.info(
            "course-selection model rejected input error=%s courses=%s",
            type(exc).__name__,
            len(request.market.courses),
        )
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        error_id = log_application_error("course_selection.optimize", exc, 500)
        raise HTTPException(status_code=500, detail=f"模型计算失败（错误编号：{error_id}）") from exc

    elapsed_ms = (time.perf_counter() - started_at) * 1000.0
    logger.info(
        "course-selection model=%s courses=%s status=%s elapsed_ms=%.1f",
        result["model_version"],
        len(request.market.courses),
        result["solution_status"],
        elapsed_ms,
    )
    return CourseSelectionOptimizeResponse.model_validate(result)
