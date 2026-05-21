from typing import Optional
from fastapi import APIRouter, HTTPException, Query

from backend.app.dependencies import (
    _storage, _academic_storage, _auto_login, _api_logger, COOKIE_FILE,
    get_auth_client, set_auth_client
)
from backend.app.schemas import LoginRequest, LoginResponse
from backend.core.auth import NEUAuthClient

router = APIRouter()


@router.get("/api/status")
async def get_status():
    """获取登录状态和存储信息"""
    client = get_auth_client()

    storage_info = _storage.get_storage_info()
    has_credentials = _storage.load_credentials() is not None
    last_update = _storage.get_last_update_time()

    return {
        "is_logged_in": client is not None and client.is_logged_in,
        "has_credentials": has_credentials,
        "has_local_data": storage_info["csv_count"] > 0,
        "last_update": last_update.isoformat() if last_update else None,
        "storage": storage_info,
        "current_user": client.username if client else None
    }


@router.post("/api/login", response_model=LoginResponse)
async def login(request: LoginRequest):
    """登录接口"""
    try:
        # 创建客户端，启用 Cookie 持久化
        client = NEUAuthClient(
            request.username,
            request.password,
            cookie_file=COOKIE_FILE
        )
        success = client.login()

        if success:
            set_auth_client(client)

            # 保存凭证
            if request.remember:
                _auto_login.save_login(client)

            # 自动获取并保存成绩（后台执行，不阻塞登录）
            try:
                _academic_storage.refresh_scores(client)
            except Exception as e:
                print(f"自动保存成绩失败: {e}")

            return LoginResponse(
                success=True,
                message="登录成功",
                username=request.username
            )
        else:
            return LoginResponse(
                success=False,
                message="登录失败"
            )

    except Exception as e:
        return LoginResponse(
            success=False,
            message=f"登录错误: {str(e)}"
        )


@router.post("/api/logout")
async def logout(clear_data: bool = Query(True, description="是否清理用户数据")):
    """
    登出接口

    Args:
        clear_data: 是否清理用户数据（成绩、培养计划、头像等），默认 True
    """
    global _auth_client

    result = {"success": True, "message": "已登出"}

    # 清除客户端的 cookie
    if _auth_client:
        _auth_client.clear_cookies()

    set_auth_client(None)
    _auto_login.clear_login()

    # 清理用户数据（保留登录配置）
    if clear_data:
        try:
            clear_result = _storage.clear_all_data(preserve_config=True)
            _api_logger.info(f"[Logout] 清理数据: 删除 {clear_result['deleted_count']} 个文件, 保留 {clear_result['preserved_count']} 个配置")
            result["data_cleared"] = True
            result["cleared_files"] = clear_result["deleted_count"]
        except Exception as e:
            _api_logger.error(f"[Logout] 清理数据失败: {e}")
            result["data_cleared"] = False
            result["clear_error"] = str(e)

    return result
