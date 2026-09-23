from fastapi import APIRouter, Depends

from backend.app.cache_support import read_cache
from backend.app.dependencies import require_cached_auth_identity
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
