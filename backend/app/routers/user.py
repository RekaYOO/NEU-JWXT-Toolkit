from fastapi import APIRouter, HTTPException, Depends, Query
from fastapi.responses import Response

from backend.core.auth import NEUAuthClient
from backend.app.dependencies import require_cached_auth_identity, require_serialized_auth
from backend.app.cache_support import read_cache, submit_refresh, wait_for_job
from backend.core.cache.resources import avatar_bytes
from backend.core.log import log_application_error

router = APIRouter()


@router.get("/user/info")
def get_user_info(auth: NEUAuthClient = Depends(require_serialized_auth)):
    """获取当前用户信息（包含头像）"""
    try:
        user_info = auth.get_user_info()
        if not user_info:
            raise HTTPException(status_code=500, detail="获取用户信息失败")
        return user_info
    except Exception as e:
        error_id = log_application_error("user.info", e, 500)
        raise HTTPException(status_code=500, detail=f"获取用户信息失败（错误编号：{error_id}）") from e


@router.get("/user/avatar")
def get_user_avatar(
    refresh: bool = Query(False, description="强制刷新头像"),
    auth: NEUAuthClient = Depends(require_cached_auth_identity)
):
    """
    获取用户头像图片

    支持缓存：默认使用本地缓存，refresh=true 时强制从服务器获取
    """
    try:
        entry, stale = read_cache(auth.username, "avatar")
        submission = None
        if refresh or stale:
            submission = submit_refresh(
                auth.username,
                "avatar",
                force=refresh,
                reason="manual" if refresh else "page_swr",
            )
        if entry is None or refresh:
            wait_for_job(submission.job_id if submission else None, timeout=30)
            entry, stale = read_cache(auth.username, "avatar")
        if entry is None:
            raise HTTPException(status_code=404, detail="头像不存在")
        data = avatar_bytes(entry.payload)
        return Response(
            content=data,
            media_type="image/png",
            headers={
                "ETag": f'"{entry.revision}"',
                "X-Cache-Stale": "true" if stale else "false",
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        error_id = log_application_error("user.avatar", e, 500)
        raise HTTPException(status_code=500, detail=f"获取头像失败（错误编号：{error_id}）") from e
