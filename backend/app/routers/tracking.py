"""Grade tracking configuration, status, and manual actions."""

from fastapi import APIRouter, Depends, HTTPException

from backend.app.dependencies import get_grade_tracker
from backend.app.schemas.tracking import (
    GradeTrackingConfigUpdate,
    GradeTrackingEnabledUpdate,
    GradeTrackingRecoveryCaptchaRequest,
    GradeTrackingRecoverySMSRequest,
)
from backend.core.log import log_application_error, log_security_event


router = APIRouter()


def _recovery_error(error: Exception) -> HTTPException:
    if isinstance(error, ValueError):
        return HTTPException(status_code=404, detail=str(error))
    error_code = getattr(error, "error_code", None)
    if error_code:
        return HTTPException(
            status_code=409,
            detail={"message": str(error), "error_code": error_code},
        )
    return HTTPException(status_code=503, detail="登录恢复服务暂时不可用")


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
                # This endpoint is also used by the tracking page, which only
                # edits schedule/login fields. Do not let schema defaults for
                # SMTP fields overwrite the system mail configuration.
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


@router.post("/test-email")
def test_tracking_email(tracker=Depends(get_grade_tracker)):
    try:
        tracker.test_email()
        return {"success": True, "message": "测试邮件已发送"}
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:
        error_id = log_application_error("tracking.test_email", error, 502)
        raise HTTPException(status_code=502, detail=f"测试邮件发送失败（错误编号：{error_id}）") from error


@router.get("/recovery/{token}/status")
def get_recovery_status(token: str, tracker=Depends(get_grade_tracker)):
    try:
        return tracker.get_recovery_status(token)
    except Exception as error:
        if not isinstance(error, ValueError):
            log_application_error("tracking.recovery_status", error, 503)
        raise _recovery_error(error) from error


@router.post("/recovery/{token}/start")
def start_recovery_login(token: str, tracker=Depends(get_grade_tracker)):
    try:
        result = tracker.start_recovery_login(token)
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
def poll_recovery_login(token: str, tracker=Depends(get_grade_tracker)):
    try:
        result = tracker.poll_recovery_login(token)
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


@router.post("/recovery/{token}/captcha/refresh")
def refresh_recovery_captcha(token: str, tracker=Depends(get_grade_tracker)):
    try:
        return tracker.refresh_recovery_captcha(token)
    except Exception as error:
        if not isinstance(error, ValueError):
            log_application_error("tracking.recovery_captcha_refresh", error, 503)
        raise _recovery_error(error) from error


@router.post("/recovery/{token}/sms/send")
def send_recovery_sms(
    token: str,
    payload: GradeTrackingRecoveryCaptchaRequest,
    tracker=Depends(get_grade_tracker),
):
    try:
        return tracker.send_recovery_sms(token, payload.captcha_code)
    except Exception as error:
        if not isinstance(error, ValueError):
            log_application_error("tracking.recovery_sms_send", error, 503)
        log_security_event(
            "tracking_recovery_sms",
            "failure",
            reason="sms_send_failed",
            auth_method="sms",
            error_type=type(error).__name__,
        )
        raise _recovery_error(error) from error


@router.post("/recovery/{token}/sms/verify")
def verify_recovery_sms(
    token: str,
    payload: GradeTrackingRecoverySMSRequest,
    tracker=Depends(get_grade_tracker),
):
    try:
        result = tracker.verify_recovery_sms(
            token, payload.code, payload.trust_device
        )
        if result.get("status") == "authenticated":
            log_security_event(
                "tracking_recovery_login",
                "success",
                auth_method="recovery_sms",
                trust_device=payload.trust_device,
            )
        return result
    except Exception as error:
        if not isinstance(error, ValueError):
            log_application_error("tracking.recovery_sms_verify", error, 503)
        log_security_event(
            "tracking_recovery_login",
            "failure",
            reason="sms_verify_failed",
            auth_method="recovery_sms",
            error_type=type(error).__name__,
        )
        raise _recovery_error(error) from error


@router.post("/recovery/{token}/cancel")
def cancel_recovery_login(token: str, tracker=Depends(get_grade_tracker)):
    try:
        return tracker.cancel_recovery_login(token)
    except Exception as error:
        if not isinstance(error, ValueError):
            log_application_error("tracking.recovery_cancel", error, 503)
        raise _recovery_error(error) from error
