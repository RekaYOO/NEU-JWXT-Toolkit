from typing import Optional
from fastapi import APIRouter, HTTPException, Query

from backend.app.dependencies import (
    _storage, _academic_storage, _auto_login, _api_logger, COOKIE_FILE,
    get_auth_client, peek_auth_client, set_auth_client
)
from backend.app.schemas import LoginRequest, LoginResponse, WebVPNQRStartRequest, WebVPNQRStatusRequest
from backend.core.auth import NEUAuthClient
from backend.core.auth.client import WebVPNLoginError, WebVPNRequiredError

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
        "current_user": client.username if client else None,
        "network_mode": client.active_mode if client else "auto",
    }


@router.post("/api/login", response_model=LoginResponse)
async def login(request: LoginRequest):
    """登录接口"""
    try:
        # 创建客户端，启用 Cookie 持久化
        client = NEUAuthClient(
            request.username,
            request.password,
            cookie_file=COOKIE_FILE,
            network_mode=request.network_mode,
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
                username=request.username,
                network_mode=client.active_mode,
            )
        else:
            return LoginResponse(
                success=False,
                message="登录失败"
            )

    except WebVPNRequiredError as e:
        return LoginResponse(
            success=False,
            message=str(e),
            requires_webvpn=True,
            network_mode="webvpn",
        )
    except Exception as e:
        return LoginResponse(
            success=False,
            message=f"登录错误: {str(e)}"
        )


@router.post("/api/webvpn/qr/start")
async def start_webvpn_qr_login(request: WebVPNQRStartRequest):
    """Create an application-managed QR login session for WebVPN."""
    try:
        client = NEUAuthClient(
            username=request.username or "",
            cookie_file=COOKIE_FILE,
            network_mode="webvpn",
        )
        flow = client.start_webvpn_qr_login()
        set_auth_client(client)
        return {"success": True, **flow}
    except Exception as e:
        return {"success": False, "message": f"无法启动 WebVPN 二维码登录: {e}"}


@router.post("/api/webvpn/qr/status")
async def get_webvpn_qr_status(request: WebVPNQRStatusRequest):
    """Poll an in-memory WebVPN QR flow without creating a second session."""
    client = peek_auth_client()
    if client is None:
        return {"success": False, "status": "missing", "message": "二维码登录流程不存在"}
    try:
        result = client.poll_webvpn_qr_login(request.flow_id)
        return {"success": True, **result}
    except WebVPNLoginError as e:
        return {
            "success": False,
            "status": "error",
            "message": str(e),
            "diagnostics": client.get_webvpn_qr_diagnostics(),
        }


@router.post("/api/webvpn/qr/cancel")
async def cancel_webvpn_qr_login(request: WebVPNQRStatusRequest):
    client = peek_auth_client()
    if client:
        client.cancel_webvpn_qr_login(request.flow_id)
    return {"success": True}


@router.get("/api/webvpn/qr/diagnostics")
async def get_webvpn_qr_diagnostics():
    """Temporary local-only view of non-secret QR redirect metadata."""
    client = peek_auth_client()
    return {"success": client is not None, "diagnostics": client.get_webvpn_qr_diagnostics() if client else {}}


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
