"""Local-only aggregate snapshots for the browser application shell."""

from __future__ import annotations

import base64
from typing import Optional

from fastapi import APIRouter, Query
from fastapi.encoders import jsonable_encoder

from backend.app.dependencies import (
    _cache_coordinator,
    _cache_store,
    get_auth_generation,
    peek_auth_client,
)
from backend.app.client_snapshot import (
    cache_events_snapshot,
    pending_auth_challenge_snapshot,
    timetable_bootstrap_snapshot,
)
from backend.core.cache.resources import avatar_bytes, avatar_token
from backend.core.runtime import get_runtime_config
from backend.core.runtime.capabilities import MOBILE_API_VERSION, runtime_capabilities


router = APIRouter(prefix="/client", tags=["client-bootstrap"])
_config = get_runtime_config()
_SUPPORTED_CHANNELS = frozenset({"auth", "cache"})


def _active_identity():
    client = peek_auth_client()
    if (
        client is None
        or not getattr(client, "is_logged_in", False)
        or not str(getattr(client, "username", "") or "")
    ):
        return None, ""
    return client, str(client.username)


def _auth_snapshot(client, account: str) -> dict:
    source = client or peek_auth_client()
    return {
        "is_logged_in": bool(client and account),
        "current_user": account or None,
        "network_mode": getattr(client, "active_mode", "direct") if client else "direct",
        "identity_epoch": get_auth_generation(),
        "error_code": getattr(source, "_last_webvpn_error_code", None) or None,
        "error_message": getattr(source, "_last_webvpn_error_message", None) or None,
    }


def _avatar_snapshot(account: str) -> dict | None:
    if not account:
        return None
    result = _cache_coordinator.read_many(
        account_id=account,
        resources=(("avatar", "default"),),
    )
    entry, stale = result.get(("avatar", "default"), (None, True))
    if entry is None:
        return None
    try:
        return {
            "image_base64": base64.b64encode(avatar_bytes(entry.payload)).decode("ascii"),
            "media_type": "image/png",
            "token": avatar_token(entry.payload),
            "revision": entry.revision,
            "stale": stale,
            "saved_at": entry.saved_at.isoformat(),
            "last_checked_at": entry.last_checked_at.isoformat() if entry.last_checked_at else None,
        }
    except Exception:
        return None


@router.get("/bootstrap")
def get_client_bootstrap():
    """Return application-shell state without touching the remote Session."""
    client, account = _active_identity()
    timetable = timetable_bootstrap_snapshot(account) if client is not None else None
    return {
        "schema_version": 1,
        "runtime": {
            "status": "ok",
            "version": _config.version,
            "profile": _config.profile,
            "mobile_api_version": MOBILE_API_VERSION,
            "capabilities": runtime_capabilities(_config),
        },
        "auth": _auth_snapshot(client, account),
        "pending_auth": pending_auth_challenge_snapshot(),
        "cache_cursor": _cache_store.latest_event_cursor(account) if account else 0,
        "timetable": jsonable_encoder(timetable) if timetable is not None else None,
        "avatar": _avatar_snapshot(account),
    }


@router.get("/updates")
def get_client_updates(
    cursor: Optional[int] = Query(None, ge=0),
    channels: str = Query("cache,auth", max_length=64),
):
    """Return due local state channels in one bounded response."""
    requested = {
        item.strip().lower()
        for item in channels.split(",")
        if item.strip().lower() in _SUPPORTED_CHANNELS
    }
    client, account = _active_identity()
    result = {
        "schema_version": 1,
        "auth": _auth_snapshot(client, account),
    }
    if "auth" in requested:
        result["pending_auth"] = pending_auth_challenge_snapshot()
    if "cache" in requested:
        result["cache"] = (
            cache_events_snapshot(account, cursor, limit=200)
            if account
            else {"events": [], "cursor": 0}
        )
    return result
