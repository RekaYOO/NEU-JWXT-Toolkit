"""Loopback-only integration surface for the Android local shell."""

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.app.dependencies import (
    get_course_selection_automation_service,
    get_grade_tracker,
    get_system_mail_service,
)
from backend.core.notifications import MobileNotificationService
from backend.core.runtime import get_runtime_config


router = APIRouter(prefix="/mobile", tags=["mobile-shell"])
_config = get_runtime_config()


def _notifications(service=Depends(get_system_mail_service)) -> MobileNotificationService:
    if not _config.mobile_mode or not isinstance(service, MobileNotificationService):
        raise HTTPException(status_code=404, detail="该运行模式不提供移动端接口")
    return service


@router.get("/notifications")
def pending_notifications(
    limit: int = Query(50, ge=1, le=100),
    service: MobileNotificationService = Depends(_notifications),
):
    return {"notifications": service.pending(limit)}


@router.post("/notifications/{message_id}/ack")
def acknowledge_notification(
    message_id: str,
    service: MobileNotificationService = Depends(_notifications),
):
    if not service.acknowledge(message_id):
        raise HTTPException(status_code=404, detail="通知不存在或已处理")
    return {"success": True}


@router.get("/background-state")
def background_state(
    tracking=Depends(get_grade_tracker),
    automation=Depends(get_course_selection_automation_service),
):
    if not _config.mobile_mode:
        raise HTTPException(status_code=404, detail="该运行模式不提供移动端接口")
    tracking_enabled = bool(tracking.get_status().get("enabled"))
    automation_enabled = bool(automation.has_active_tasks())
    return {
        "required": tracking_enabled or automation_enabled,
        "reasons": {
            "grade_tracking": tracking_enabled,
            "course_selection": automation_enabled,
        },
    }
