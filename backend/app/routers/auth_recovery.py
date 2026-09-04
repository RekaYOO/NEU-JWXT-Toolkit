"""Public token-scoped endpoints for remote authentication recovery."""

from fastapi import APIRouter, Depends, HTTPException

from backend.app.dependencies import get_auth_recovery_service
from backend.app.schemas.auth_recovery import AuthRecoveryCaptchaRequest, AuthRecoverySMSRequest
from backend.core.log import log_application_error, log_security_event


router = APIRouter()


def _recovery_error(error: Exception) -> HTTPException:
    if isinstance(error, ValueError):
        return HTTPException(status_code=404, detail=str(error))
    error_code = getattr(error, "error_code", None)
    if error_code:
        return HTTPException(status_code=409, detail={"message": str(error), "error_code": error_code})
    return HTTPException(status_code=503, detail="登录恢复服务暂时不可用")


def _call(operation: str, callback, *, security_event: str = "", auth_method: str = ""):
    try:
        return callback()
    except Exception as error:
        if not isinstance(error, ValueError):
            log_application_error(f"auth_recovery.{operation}", error, 503)
        if security_event:
            log_security_event(
                security_event,
                "failure",
                reason=f"{operation}_failed",
                auth_method=auth_method,
                error_type=type(error).__name__,
            )
        raise _recovery_error(error) from error


@router.get("/{token}/status")
def get_status(token: str, service=Depends(get_auth_recovery_service)):
    return _call("status", lambda: service.get_status(token))


@router.post("/{token}/start")
def start(token: str, service=Depends(get_auth_recovery_service)):
    result = _call(
        "start", lambda: service.start(token),
        security_event="auth_recovery_login", auth_method="recovery_qr",
    )
    log_security_event("auth_recovery_login", "pending", auth_method="recovery_qr")
    return result


@router.get("/{token}/poll")
def poll(token: str, service=Depends(get_auth_recovery_service)):
    result = _call("poll", lambda: service.poll(token))
    if result.get("status") == "authenticated":
        log_security_event("auth_recovery_login", "success", auth_method="recovery_qr")
    return result


@router.post("/{token}/captcha/refresh")
def refresh_captcha(token: str, service=Depends(get_auth_recovery_service)):
    return _call("captcha_refresh", lambda: service.refresh_captcha(token))


@router.post("/{token}/sms/send")
def send_sms(token: str, payload: AuthRecoveryCaptchaRequest, service=Depends(get_auth_recovery_service)):
    result = _call(
        "sms_send", lambda: service.send_sms(token, payload.captcha_code),
        security_event="auth_recovery_sms", auth_method="sms",
    )
    log_security_event(
        "auth_recovery_sms",
        "success" if result.get("status") == "sent" else "failure",
        reason=None if result.get("status") == "sent" else "sms_not_sent",
        auth_method="sms",
    )
    return result


@router.post("/{token}/sms/verify")
def verify_sms(token: str, payload: AuthRecoverySMSRequest, service=Depends(get_auth_recovery_service)):
    result = _call(
        "sms_verify",
        lambda: service.verify_sms(token, payload.code, payload.trust_device),
        security_event="auth_recovery_login", auth_method="recovery_sms",
    )
    if result.get("status") == "authenticated":
        log_security_event("auth_recovery_login", "success", auth_method="recovery_sms", trust_device=payload.trust_device)
    else:
        log_security_event(
            "auth_recovery_login", "failure",
            reason="sms_not_verified", auth_method="recovery_sms",
            trust_device=payload.trust_device,
        )
    return result


@router.post("/{token}/cancel")
def cancel(token: str, service=Depends(get_auth_recovery_service)):
    return _call("cancel", lambda: service.cancel(token))
