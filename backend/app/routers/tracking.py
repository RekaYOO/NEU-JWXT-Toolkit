"""Grade tracking configuration, status, and manual actions."""

from fastapi import APIRouter, Depends, HTTPException

from backend.app.dependencies import get_grade_tracker
from backend.app.schemas.tracking import GradeTrackingConfigUpdate, GradeTrackingEnabledUpdate
from backend.core.log import log_application_error


router = APIRouter()


@router.get("/config")
def get_tracking_config(tracker=Depends(get_grade_tracker)):
    return tracker.get_config()


@router.put("/config")
def update_tracking_config(
    payload: GradeTrackingConfigUpdate,
    tracker=Depends(get_grade_tracker),
):
    try:
        return {
            "success": True,
            "config": tracker.update_config(
                payload.model_dump(exclude_unset=True, exclude_none=True)
            ),
        }
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.patch("/enabled")
def update_tracking_enabled(
    payload: GradeTrackingEnabledUpdate,
    tracker=Depends(get_grade_tracker),
):
    try:
        return {
            "success": True,
            "config": tracker.set_enabled(payload.enabled),
        }
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/status")
def get_tracking_status(tracker=Depends(get_grade_tracker)):
    return tracker.get_status()


@router.post("/check")
def check_grades_now(tracker=Depends(get_grade_tracker)):
    try:
        return {"success": True, "result": tracker.check_now()}
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except Exception as error:
        error_id = log_application_error("tracking.check", error, 503)
        raise HTTPException(status_code=503, detail=f"成绩检查失败（错误编号：{error_id}）") from error
