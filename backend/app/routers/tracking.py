"""Grade tracking configuration, status, and manual actions."""

from fastapi import APIRouter, HTTPException

from backend.app.dependencies import _grade_tracker
from backend.app.schemas.tracking import (
    GradeTrackingConfigUpdate,
    GradeTrackingEnabledUpdate,
)
from backend.core.log import log_application_error, log_security_event


router = APIRouter()


def _recovery_error(error: Exception) -> HTTPException:
    if isinstance(error, ValueError):
        return HTTPException(status_code=404, detail=str(error))
    return HTTPException(status_code=503, detail="暂时无法创建登录二维码")


@router.get("/config")
def get_tracking_config():
    return _grade_tracker.get_config()


@router.put("/config")
def update_tracking_config(payload: GradeTrackingConfigUpdate):
    try:
        return {
            "success": True,
            "config": _grade_tracker.update_config(
                payload.model_dump(exclude_none=True)
            ),
        }
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.patch("/enabled")
def update_tracking_enabled(payload: GradeTrackingEnabledUpdate):
    try:
        return {
            "success": True,
            "config": _grade_tracker.set_enabled(payload.enabled),
        }
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/status")
def get_tracking_status():
    return _grade_tracker.get_status()


@router.post("/check")
def check_grades_now():
    try:
        return {"success": True, "result": _grade_tracker.check_now()}
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except Exception as error:
        error_id = log_application_error("tracking.check", error, 503)
        raise HTTPException(status_code=503, detail=f"成绩检查失败（错误编号：{error_id}）") from error


@router.post("/test-email")
def test_tracking_email():
    try:
        _grade_tracker.test_email()
        return {"success": True, "message": "测试邮件已发送"}
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:
        error_id = log_application_error("tracking.test_email", error, 502)
        raise HTTPException(status_code=502, detail=f"测试邮件发送失败（错误编号：{error_id}）") from error


@router.get("/recovery/{token}/status")
def get_recovery_status(token: str):
    try:
        return _grade_tracker.get_recovery_status(token)
    except Exception as error:
        if not isinstance(error, ValueError):
            log_application_error("tracking.recovery_status", error, 503)
        raise _recovery_error(error) from error


@router.post("/recovery/{token}/start")
def start_recovery_login(token: str):
    try:
        result = _grade_tracker.start_recovery_login(token)
        log_security_event("tracking_recovery_login", "pending", auth_method="recovery_qr")
        return result
    except Exception as error:
        if not isinstance(error, ValueError):
            log_application_error("tracking.recovery_start", error, 503)
        log_security_event(
            "tracking_recovery_login",
            "failure",
            reason="recovery_start_failed",
            auth_method="recovery_qr",
            error_type=type(error).__name__,
        )
        raise _recovery_error(error) from error


@router.get("/recovery/{token}/poll")
def poll_recovery_login(token: str):
    try:
        result = _grade_tracker.poll_recovery_login(token)
        if isinstance(result, dict) and result.get("status") == "authenticated":
            log_security_event("tracking_recovery_login", "success", auth_method="recovery_qr")
        return result
    except Exception as error:
        if not isinstance(error, ValueError):
            log_application_error("tracking.recovery_poll", error, 503)
        log_security_event(
            "tracking_recovery_login",
            "failure",
            reason="recovery_poll_failed",
            auth_method="recovery_qr",
            error_type=type(error).__name__,
        )
        raise _recovery_error(error) from error
