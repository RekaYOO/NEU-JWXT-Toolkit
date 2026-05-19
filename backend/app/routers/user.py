from fastapi import APIRouter, HTTPException, Depends, Query
from fastapi.responses import Response

from backend.app.dependencies import _storage, _api_logger
from backend.core.auth import NEUAuthClient
from backend.app.dependencies import require_auth

router = APIRouter()


@router.get("/user/info")
async def get_user_info(auth: NEUAuthClient = Depends(require_auth)):
    """获取当前用户信息（包含头像）"""
    try:
        user_info = auth.get_user_info()
        if not user_info:
            raise HTTPException(status_code=500, detail="获取用户信息失败")
        return user_info
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取用户信息失败: {str(e)}")


@router.get("/user/avatar")
async def get_user_avatar(
    refresh: bool = Query(False, description="强制刷新头像"),
    auth: NEUAuthClient = Depends(require_auth)
):
    """
    获取用户头像图片

    支持缓存：默认使用本地缓存，refresh=true 时强制从服务器获取
    """
    try:
        _api_logger.info(f"[Avatar] 开始获取头像, user={auth.username}, refresh={refresh}")

        # 首先获取用户信息（包含头像token）
        user_info = auth.get_user_info()

        if not user_info:
            _api_logger.warning("[Avatar] 获取用户信息失败")
            raise HTTPException(status_code=404, detail="获取用户信息失败")

        avatar_token = user_info.get('avatar_token')
        if not avatar_token:
            _api_logger.warning("[Avatar] 无头像token")
            raise HTTPException(status_code=404, detail="用户未上传头像")

        # 检查本地缓存
        if not refresh:
            cached_avatar = _storage.load_avatar()
            if cached_avatar and _storage.is_avatar_valid(auth.username, avatar_token):
                _api_logger.info(f"[Avatar] 使用本地缓存，大小: {len(cached_avatar)} bytes")
                return Response(content=cached_avatar, media_type="image/png")

        # 从服务器获取
        avatar_data = auth.get_avatar(avatar_token)
        if not avatar_data:
            _api_logger.warning("[Avatar] 获取头像数据失败")
            raise HTTPException(status_code=404, detail="头像不存在")

        # 保存到本地缓存
        try:
            _storage.save_avatar(avatar_data, auth.username, avatar_token)
            _api_logger.info(f"[Avatar] 已缓存头像，大小: {len(avatar_data)} bytes")
        except Exception as cache_error:
            _api_logger.warning(f"[Avatar] 缓存头像失败: {cache_error}")

        return Response(content=avatar_data, media_type="image/png")
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        error_msg = f"[Avatar] 获取头像失败: {e}"
        trace = traceback.format_exc()
        _api_logger.error(error_msg)
        _api_logger.error(trace)
        raise HTTPException(status_code=500, detail=f"获取头像失败: {str(e)}")
