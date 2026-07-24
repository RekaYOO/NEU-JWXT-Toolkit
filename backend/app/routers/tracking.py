"""Grade tracking configuration, status, and manual actions."""

from fastapi import APIRouter, HTTPException

from backend.app.dependencies import _grade_tracker
from backend.app.schemas.tracking import GradeTrackingConfigUpdate


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
        raise HTTPException(status_code=503, detail=f"成绩检查失败：{error}") from error


@router.post("/test-email")
def test_tracking_email():
    try:
        _grade_tracker.test_email()
        return {"success": True, "message": "测试邮件已发送"}
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"测试邮件发送失败：{error}") from error


@router.get("/recovery/{token}/status")
def get_recovery_status(token: str):
    try:
        return _grade_tracker.get_recovery_status(token)
    except Exception as error:
        raise _recovery_error(error) from error


@router.post("/recovery/{token}/start")
def start_recovery_login(token: str):
    try:
        return _grade_tracker.start_recovery_login(token)
    except Exception as error:
        raise _recovery_error(error) from error


@router.get("/recovery/{token}/poll")
def poll_recovery_login(token: str):
    try:
        return _grade_tracker.poll_recovery_login(token)
    except Exception as error:
        raise _recovery_error(error) from error
