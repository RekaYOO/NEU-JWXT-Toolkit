from fastapi import APIRouter, Depends, HTTPException

from backend.app.cache_support import mark_system_messages_read_cache, read_cache
from backend.app.dependencies import (
    require_cached_auth_identity,
    require_mutation_auth,
)
from backend.app.schemas.system_messages import SystemMessageReadRequest
from backend.core.academic.system_messages import SystemMessageAPI
from backend.core.auth import NEUAuthClient

router = APIRouter()


@router.get("/system-messages/cache")
def get_system_messages_cache(
    auth: NEUAuthClient = Depends(require_cached_auth_identity),
):
    entry, stale = read_cache(str(auth.username), "system-messages")
    if not entry:
        return {
            "available": False,
            "messages": [],
            "cache": {},
        }
    return {
        "available": True,
        "messages": (entry.payload or {}).get("messages") or [],
        "cache": entry.metadata(is_stale=stale),
    }


@router.post("/system-messages/read")
def mark_system_messages_read(
    request: SystemMessageReadRequest,
    auth: NEUAuthClient = Depends(require_mutation_auth),
):
    """Synchronize the user's acknowledgement with the official message center."""
    api = SystemMessageAPI(auth)
    results = []
    failures = []
    for item in request.messages:
        try:
            api.mark_read(item.message_id, item.kind)
            results.append({"message_id": item.message_id, "kind": item.kind})
        except Exception as error:
            failures.append({
                "message_id": item.message_id,
                "kind": item.kind,
                "error": str(error),
            })
    if failures and not results:
        raise HTTPException(status_code=502, detail="教务系统消息已读同步失败")
    if results:
        mark_system_messages_read_cache(str(auth.username), results)
    return {"success": True, "marked": results, "failed": failures}
