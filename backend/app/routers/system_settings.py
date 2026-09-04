from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from backend.app.dependencies import (
    get_auth_recovery_service,
    get_cache_coordinator,
    get_storage,
    get_system_mail_service,
)
from backend.app.schemas.system_settings import (
    CacheSettingsUpdate,
    CacheResourceSetting,
    SystemSettingsResponse,
    SystemMailConfigUpdate,
    AuthRecoveryConfigUpdate,
)

router = APIRouter(prefix="/system-settings", tags=["system-settings"])

_MAX_INTERVAL_MINUTES = 52_560_000


def _safe_interval_minutes(value, fallback=5):
    """Normalize cache durations before they enter the response schema."""
    try:
        value = int(value)
    except (TypeError, ValueError):
        value = fallback
    return max(1, min(_MAX_INTERVAL_MINUTES, value))


def _defaults(coordinator):
    result = {}
    for resource in coordinator.registry.resources():
        spec = coordinator.registry.get(resource)
        result[resource] = CacheResourceSetting(
            enabled=True,
            interval_minutes=_safe_interval_minutes(spec.max_age.total_seconds() // 60),
        )
    return result


def _read(storage, coordinator):
    defaults = _defaults(coordinator)
    config = storage.load_config()
    saved = config.get("cache_settings", {}) if isinstance(config, dict) else {}
    for resource, value in saved.items() if isinstance(saved, dict) else ():
        if resource not in defaults or not isinstance(value, dict):
            continue
        defaults[resource] = CacheResourceSetting(**{
            "enabled": value.get("enabled", defaults[resource].enabled),
            "interval_minutes": _safe_interval_minutes(
                value.get("interval_minutes", defaults[resource].interval_minutes),
                defaults[resource].interval_minutes,
            ),
        })
    return defaults


def _apply(storage, coordinator, settings):
    config = storage.load_config()
    config = dict(config) if isinstance(config, dict) else {}
    config["cache_settings"] = {
        resource: value.model_dump() for resource, value in settings.items()
    }
    storage.save_config(config)
    coordinator.set_policies({
        resource: {
            "enabled": value.enabled,
            "interval_seconds": value.interval_minutes * 60,
        }
        for resource, value in settings.items()
    })
    return SystemSettingsResponse(cache=settings)


@router.get("/cache", response_model=SystemSettingsResponse)
def get_cache_settings(storage=Depends(get_storage), coordinator=Depends(get_cache_coordinator)):
    settings = _read(storage, coordinator)
    _apply(storage, coordinator, settings)
    return SystemSettingsResponse(cache=settings)


@router.put("/cache", response_model=SystemSettingsResponse)
def update_cache_settings(payload: CacheSettingsUpdate, storage=Depends(get_storage), coordinator=Depends(get_cache_coordinator)):
    current = _read(storage, coordinator)
    for resource, value in payload.resources.items():
        if resource not in current:
            raise HTTPException(status_code=400, detail=f"未知缓存资源: {resource}")
        current[resource] = value
    return _apply(storage, coordinator, current)


@router.get("/mail")
def get_mail_settings(service=Depends(get_system_mail_service)):
    return service.get_config()


@router.put("/mail")
def update_mail_settings(payload: SystemMailConfigUpdate, service=Depends(get_system_mail_service)):
    try:
        return {"success": True, "config": service.update_config(payload.model_dump(exclude_none=True))}
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.post("/mail/test")
def test_mail_settings(service=Depends(get_system_mail_service)):
    try:
        service.test_email()
        return {"success": True, "message": "测试邮件已发送"}
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=502, detail="测试邮件发送失败") from error


@router.get("/auth-recovery")
def get_auth_recovery_settings(service=Depends(get_auth_recovery_service)):
    return service.get_config()


@router.put("/auth-recovery")
def update_auth_recovery_settings(payload: AuthRecoveryConfigUpdate, service=Depends(get_auth_recovery_service)):
    try:
        return {"success": True, "config": service.update_config(payload.model_dump())}
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
