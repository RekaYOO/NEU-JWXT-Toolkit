"""Stateless API for the new course-weight game model."""

from __future__ import annotations

import logging
from threading import BoundedSemaphore
import time
import requests

from fastapi import APIRouter, HTTPException, Response, Depends

from backend.app.schemas.course_selection import (
    CourseSelectionOptimizeRequest,
    CourseSelectionOptimizeResponse,
    JwxkSettingsUpdate,
    JwxkStatusResponse,
)
from backend.app.dependencies import get_storage, peek_auth_client, remote_session_guard
from backend.core.auth.client import NEULoginError
from backend.core.course_selection import (
    CourseMarket,
    CourseSelectionError,
    MarketSnapshot,
    SelectionPolicy,
    optimize_course_weights,
    JWXK_CAS_SERVICE,
    JwxkError,
    JwxkPublicClient,
    resolve_network_mode,
)
from backend.core.storage import Storage
from backend.core.course_selection.model import TieRule
from backend.core.log import log_application_error


router = APIRouter(prefix="/course-selection", tags=["course-selection"])
logger = logging.getLogger(__name__)
_solver_slots = BoundedSemaphore(value=2)
_JWXK_CONFIG_KEY = "course_selection"


def _read_jwxk_preference(storage: Storage) -> str:
    config = storage.load_config()
    selection = config.get(_JWXK_CONFIG_KEY) if isinstance(config, dict) else None
    value = selection.get("network_mode") if isinstance(selection, dict) else "follow"
    return value if value in {"follow", "direct", "webvpn"} else "follow"


@router.get("/jwxk/status", response_model=JwxkStatusResponse)
def get_jwxk_status(
    response: Response,
    storage: Storage = Depends(get_storage),
) -> JwxkStatusResponse:
    response.headers["Cache-Control"] = "no-store"
    preference = _read_jwxk_preference(storage)
    primary = peek_auth_client()
    primary_mode = str(getattr(primary, "active_mode", "direct") or "direct")
    effective = resolve_network_mode(preference, primary_mode)
    primary_authenticated = bool(primary and getattr(primary, "is_logged_in", False))
    service_authenticated = False
    if primary_authenticated:
        try:
            with remote_session_guard():
                if peek_auth_client() is primary:
                    service_authenticated = primary.ensure_service_session(
                        "jwxk", network_mode_override=effective
                    )
        except (NEULoginError, JwxkError, requests.RequestException, ValueError, RuntimeError) as error:
            logger.info("jwxk service session unavailable error=%s", type(error).__name__)
    try:
        batches = [item.to_dict() for item in JwxkPublicClient().get_batches()]
        return JwxkStatusResponse(
            available=True,
            network_mode=preference,
            effective_network_mode=effective,
            cas_service=JWXK_CAS_SERVICE,
            primary_authenticated=primary_authenticated,
            service_authenticated=service_authenticated,
            authenticated=service_authenticated,
            batches=batches,
            message="当前批次未开放时，仅展示官方批次与时间窗。",
        )
    except (JwxkError, requests.RequestException) as error:
        logger.warning("jwxk public status unavailable error=%s", type(error).__name__)
        return JwxkStatusResponse(
            available=False,
            network_mode=preference,
            effective_network_mode=effective,
            cas_service=JWXK_CAS_SERVICE,
            primary_authenticated=primary_authenticated,
            service_authenticated=service_authenticated,
            authenticated=service_authenticated,
            batches=[],
            message="暂时无法读取选课系统批次，请稍后重试。",
        )


@router.put("/jwxk/settings", response_model=JwxkStatusResponse)
def update_jwxk_settings(
    request: JwxkSettingsUpdate,
    response: Response,
    storage: Storage = Depends(get_storage),
) -> JwxkStatusResponse:
    config = storage.load_config()
    config = dict(config) if isinstance(config, dict) else {}
    config[_JWXK_CONFIG_KEY] = {"network_mode": request.network_mode}
    storage.save_config(config)
    return get_jwxk_status(response, storage)


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
