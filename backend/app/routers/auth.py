from typing import Optional
from fastapi import APIRouter, HTTPException, Query

from backend.app.dependencies import (
    _cache_coordinator, _cache_registry, _cache_store,
    _storage, _auto_login, _api_logger, COOKIE_FILE,
    get_auth_client, peek_auth_client, remote_session_guard,
    logout_auth_client, schedule_login_bootstrap, set_auth_client
)
from backend.app.schemas import (
    LoginRequest, LoginResponse, WebVPNQRStartRequest, WebVPNQRStatusRequest,
    WebVPNPasswordStartRequest, WebVPNSMSCodeRequest, WebVPNSMSVerifyRequest,
)
from backend.core.auth import NEUAuthClient
from backend.core.auth.client import (
    DirectAccessError, LOGIN_ERR_WRONG_PWD, NEULoginError,
    WebVPNLoginError, WebVPNRequiredError,
)

router = APIRouter()


@router.get("/api/status")
def get_status():
    """获取登录状态和存储信息"""
    client = get_auth_client()

    storage_info = _storage.get_storage_info()
    has_credentials = _storage.load_credentials() is not None
    cached_account = _cache_store.latest_account_for(
        ("scores", "academic-report")
    )
    cache_entries = []
    if cached_account:
        for resource in ("scores", "academic-report"):
            entry, _stale = _cache_coordinator.read(
                account_id=cached_account,
                resource=resource,
            )
            spec = _cache_registry.get(resource)
            if (
                entry
                and entry.schema_version == spec.schema_version
                and entry.revision_algorithm_version
                == spec.revision_algorithm_version
                and entry.payload_type == spec.payload_type
            ):
                cache_entries.append(entry)
    legacy_last_update = _storage.get_last_update_time()
    last_update = (
        max(entry.saved_at for entry in cache_entries)
        if cache_entries
        else legacy_last_update
    )

    return {
        "is_logged_in": client is not None and client.is_logged_in,
        "has_credentials": has_credentials,
        "has_local_data": bool(cache_entries) or storage_info["csv_count"] > 0,
        "last_update": last_update.isoformat() if last_update else None,
        "storage": storage_info,
        "current_user": client.username if client else None,
        "network_mode": client.active_mode if client else "direct",
    }


@router.post("/api/login", response_model=LoginResponse)
def login(request: LoginRequest):
    """登录接口"""
    try:
        # 创建客户端，启用 Cookie 持久化
        with remote_session_guard():
            client = NEUAuthClient(
                request.username,
                request.password,
                cookie_file=COOKIE_FILE,
                network_mode=request.network_mode,
                restore_session=False,
            )
            success = client.login()
            if success:
                set_auth_client(client)
                schedule_login_bootstrap(client)
                if request.remember:
                    _auto_login.save_login(client)

        if success:
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
            error_code="DIRECT_ACCESS_FAILED",
            suggestion="校外网络请选择 WebVPN，并使用微信扫码快速登录或账号密码登录。",
        )
    except DirectAccessError as e:
        return LoginResponse(
            success=False,
            message=str(e),
            requires_webvpn=True,
            network_mode="direct",
            error_code="DIRECT_ACCESS_FAILED",
            suggestion="请检查校园网络；校外网络请选择 WebVPN。",
        )
    except NEULoginError as e:
        wrong_password = e.error_type == LOGIN_ERR_WRONG_PWD
        return LoginResponse(
            success=False,
            message=str(e),
            error_code="WRONG_PASSWORD" if wrong_password else "REQUEST_ERROR",
            suggestion="请检查学号和密码。" if wrong_password else "请稍后重试；若持续失败请查看日志。",
        )
    except Exception as e:
        return LoginResponse(
            success=False,
            message=f"登录错误: {str(e)}",
            error_code="REQUEST_ERROR",
            suggestion="请稍后重试；若持续失败请查看日志。",
        )


@router.post("/api/webvpn/qr/start")
def start_webvpn_qr_login(request: WebVPNQRStartRequest):
    """Create an application-managed QR login session for WebVPN."""
    try:
        with remote_session_guard():
            client = NEUAuthClient(
                username=request.username or "",
                cookie_file=COOKIE_FILE,
                network_mode="webvpn",
                restore_session=False,
            )
            flow = client.start_webvpn_qr_login()
            set_auth_client(client)
        return {"success": True, **flow}
    except Exception as e:
        return {"success": False, "message": f"无法启动 WebVPN 二维码登录: {e}"}


@router.post("/api/webvpn/qr/status")
def get_webvpn_qr_status(request: WebVPNQRStatusRequest):
    """Poll an in-memory WebVPN QR flow without creating a second session."""
    client = peek_auth_client()
    if client is None:
        return {"success": False, "status": "missing", "message": "二维码登录流程不存在"}
    try:
        with remote_session_guard():
            if peek_auth_client() is not client:
                return {
                    "success": False,
                    "status": "missing",
                    "message": "二维码登录流程不存在",
                }
            result = client.poll_webvpn_qr_login(request.flow_id)
            if result.get("status") == "authenticated":
                set_auth_client(client, force_epoch=True)
                schedule_login_bootstrap(client)
        return {"success": True, **result}
    except WebVPNLoginError as e:
        return {
            "success": False,
            "status": "error",
            "message": str(e),
            "diagnostics": client.get_webvpn_qr_diagnostics(),
        }


@router.post("/api/webvpn/qr/cancel")
def cancel_webvpn_qr_login(request: WebVPNQRStatusRequest):
    client = peek_auth_client()
    if client:
        with remote_session_guard():
            client.cancel_webvpn_qr_login(request.flow_id)
    return {"success": True}


def _save_webvpn_password_login(client: NEUAuthClient, remember: bool) -> None:
    # SMS completion may authenticate the same client object that was stored
    # while pending; successful login still establishes a new identity epoch.
    set_auth_client(client, force_epoch=True)
    schedule_login_bootstrap(client)
    if remember:
        _auto_login.save_login(client)


@router.post("/api/webvpn/password/start")
def start_webvpn_password_login(request: WebVPNPasswordStartRequest):
    """Start real WebVPN password login and return an SMS challenge when required."""
    try:
        with remote_session_guard():
            client = NEUAuthClient(
                request.username,
                request.password,
                cookie_file=COOKIE_FILE,
                network_mode="webvpn",
                restore_session=False,
            )
            result = client.start_webvpn_password_login()
            if result["status"] == "authenticated":
                _save_webvpn_password_login(client, request.remember)
            else:
                # The flow stays only in memory and is discarded on server restart.
                client._webvpn_sms_flow["remember"] = request.remember
                set_auth_client(client)
        return {"success": True, **result}
    except NEULoginError as error:
        return {
            "success": False, "message": str(error),
            "error_code": "WRONG_PASSWORD" if error.error_type == LOGIN_ERR_WRONG_PWD else "WEBVPN_TIMEOUT",
            "suggestion": "请检查网络；响应较慢时建议优先使用微信扫码快速登录。",
        }
    except Exception as error:
        _api_logger.exception("[WebVPN] 账号密码登录失败")
        return {
            "success": False, "message": f"WebVPN 登录失败: {error}",
            "error_code": "WEBVPN_TIMEOUT", "suggestion": "请检查网络或改用微信扫码快速登录。",
        }


@router.post("/api/webvpn/sms/send")
def send_webvpn_sms_code(request: WebVPNSMSCodeRequest):
    client = peek_auth_client()
    if client is None:
        return {"success": False, "message": "短信验证流程不存在，请重新登录"}
    try:
        with remote_session_guard():
            return {"success": True, **client.send_webvpn_sms_code(request.flow_id)}
    except WebVPNLoginError as error:
        return {"success": False, "message": str(error)}


@router.post("/api/webvpn/sms/verify")
def verify_webvpn_sms_code(request: WebVPNSMSVerifyRequest):
    client = peek_auth_client()
    if client is None:
        return {"success": False, "message": "短信验证流程不存在，请重新登录"}
    try:
        remember = bool((client._webvpn_sms_flow or {}).get("remember"))
        with remote_session_guard():
            if peek_auth_client() is not client:
                return {"success": False, "message": "短信验证流程不存在，请重新登录"}
            result = client.verify_webvpn_sms_code(request.flow_id, request.code, request.trust_device)
            _save_webvpn_password_login(client, remember)
        return {"success": True, **result}
    except WebVPNLoginError as error:
        return {"success": False, "message": str(error)}


@router.post("/api/webvpn/sms/cancel")
def cancel_webvpn_sms_login(request: WebVPNSMSCodeRequest):
    client = peek_auth_client()
    if client:
        with remote_session_guard():
            client.cancel_webvpn_sms_login(request.flow_id)
    return {"success": True}


@router.post("/api/logout")
def logout(clear_data: bool = Query(True, description="是否清理用户数据")):
    """
    登出接口

    Args:
        clear_data: 是否清理用户数据（成绩、培养计划、头像等），默认 True
    """
    result = {"success": True, "message": "已登出"}

    # 清除当前内存会话及其持久化 Cookie。
    client = peek_auth_client()
    with remote_session_guard():
        if client:
            client.cancel_webvpn_qr_login()
            client.cancel_webvpn_sms_login()
            client.clear_cookies()
            client.session.cookies.clear()
        logout_auth_client(clear_cache=clear_data)
        _auto_login.clear_login()
        # Keep file cleanup in the same session critical section. A concurrent
        # login must not save fresh cookies/credentials that an older logout
        # subsequently deletes.
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
