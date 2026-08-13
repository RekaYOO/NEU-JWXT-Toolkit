from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from backend.app.dependencies import get_cache_coordinator, get_storage
from backend.app.schemas.system_settings import (
    CacheSettingsUpdate,
    CacheResourceSetting,
    SystemSettingsResponse,
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
