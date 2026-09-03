from typing import Optional
import time
from fastapi import APIRouter, HTTPException, Query

from backend.app.dependencies import (
    _cache_coordinator, _cache_registry, _cache_store,
    _storage, _auto_login, _api_logger, COOKIE_FILE,
    clear_pending_auth_client, get_auth_client, peek_auth_client,
    peek_pending_auth_client, remote_session_guard, set_pending_auth_client,
    logout_auth_client, schedule_login_bootstrap, set_auth_client,
)
from backend.app.schemas import (
    LoginRequest, LoginResponse, WebVPNQRStartRequest, WebVPNQRStatusRequest,
    WebVPNPasswordStartRequest, WebVPNSMSCodeRequest, WebVPNSMSSendRequest, WebVPNSMSVerifyRequest,
)
from backend.core.auth import NEUAuthClient
from backend.core.auth.client import (
    DirectAccessError, LOGIN_ERR_WRONG_PWD, NEULoginError,
    WebVPNLoginError, WebVPNRequiredError,
    WEBVPN_ERR_CAPTCHA_FETCH, WEBVPN_ERR_CAPTCHA_INVALID,
    WEBVPN_ERR_FLOW_MISSING, WEBVPN_ERR_FLOW_REPLACED,
    WEBVPN_ERR_FLOW_EXPIRED, WEBVPN_ERR_SMS_INVALID,
    WEBVPN_ERR_SMS_RATE_LIMITED, WEBVPN_ERR_SMS_UNBOUND,
    WEBVPN_ERR_UPSTREAM_TIMEOUT, WEBVPN_ERR_UPSTREAM_NON_JSON,
    WEBVPN_ERR_UPSTREAM_REDIRECT, WEBVPN_ERR_SESSION_ESTABLISH,
    WEBVPN_ERR_CAMPUS_NETWORK,
    WEBVPN_ERR_UNKNOWN,
)
from backend.core.log import log_application_error, log_security_event
from backend.app.client_snapshot import pending_auth_challenge_snapshot

router = APIRouter()


def _webvpn_failure(message: str, *, error_code: str, status: str = "error", **extra):
    """Return a stable, backwards-compatible WebVPN error envelope."""
    return {
        "success": False,
        "status": status,
        "error_code": error_code,
        "message": message,
        **extra,
    }


def _error_code(error: Exception, fallback: str = WEBVPN_ERR_UNKNOWN) -> str:
    return str(getattr(error, "error_code", None) or fallback)


def _webvpn_suggestion(error_code: str) -> str:
    if error_code == WEBVPN_ERR_CAMPUS_NETWORK:
        return "请切换登录页的“校内直连”；校园网无法使用 WebVPN。"
    return "请检查网络；响应较慢时建议优先使用微信扫码快速登录。"


def _webvpn_sms_client(flow_id: str):
    """Find a password or QR candidate client that owns this SMS flow."""
    for candidate in (peek_pending_auth_client(), peek_auth_client()):
        if candidate and getattr(candidate, "_webvpn_sms_flow", None):
            if candidate._webvpn_sms_flow.get("id") == flow_id:
                return candidate
    return None


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

    status_client = client or peek_auth_client()
    auth_error_code = str(getattr(status_client, "_last_webvpn_error_code", "") or "")
    auth_error_message = str(getattr(status_client, "_last_webvpn_error_message", "") or "")
    return {
        "is_logged_in": client is not None and client.is_logged_in,
        "has_credentials": has_credentials,
        "has_local_data": bool(cache_entries) or storage_info["csv_count"] > 0,
        "last_update": last_update.isoformat() if last_update else None,
        "storage": storage_info,
        "current_user": status_client.username if status_client else None,
        "network_mode": status_client.active_mode if status_client else "direct",
        "auth_error_code": auth_error_code or None,
        "auth_error_message": auth_error_message or None,
    }


@router.get("/api/auth/pending")
def get_pending_auth_challenge():
    """Return a foreground-safe snapshot of an in-memory CAPTCHA challenge."""
    return pending_auth_challenge_snapshot(
        (peek_pending_auth_client(), peek_auth_client())
    )


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
            log_security_event(
                "neu_login",
                "success",
                subject=request.username,
                auth_method="password",
                network_mode=client.active_mode,
                remember=request.remember,
            )
            return LoginResponse(
                success=True,
                message="登录成功",
                username=request.username,
                network_mode=client.active_mode,
            )
        else:
            log_security_event(
                "neu_login",
                "failure",
                subject=request.username,
                reason="login_rejected",
                auth_method="password",
                network_mode=request.network_mode,
            )
            return LoginResponse(
                success=False,
                message="登录失败"
            )

    except WebVPNRequiredError as e:
        log_security_event(
            "neu_login",
            "failure",
            subject=request.username,
            reason="webvpn_required",
            auth_method="password",
            network_mode="direct",
            error_type=type(e).__name__,
        )
        return LoginResponse(
            success=False,
            message=str(e),
            requires_webvpn=True,
            network_mode="webvpn",
            error_code="DIRECT_ACCESS_FAILED",
            suggestion="校外网络请选择 WebVPN，并使用微信扫码快速登录或账号密码登录。",
        )
    except DirectAccessError as e:
        log_security_event(
            "neu_login",
            "failure",
            subject=request.username,
            reason="direct_access_failed",
            auth_method="password",
            network_mode="direct",
            error_type=type(e).__name__,
        )
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
        log_security_event(
            "neu_login",
            "failure",
            subject=request.username,
            reason="wrong_password" if wrong_password else "request_error",
            auth_method="password",
            network_mode=request.network_mode,
            error_code="WRONG_PASSWORD" if wrong_password else "REQUEST_ERROR",
            error_type=type(e).__name__,
        )
        return LoginResponse(
            success=False,
            message=str(e),
            error_code="WRONG_PASSWORD" if wrong_password else "REQUEST_ERROR",
            suggestion="请检查学号和密码。" if wrong_password else "请稍后重试；若持续失败请查看日志。",
        )
    except Exception as e:
        error_id = log_application_error("auth.password_login", e, 500)
        log_security_event(
            "neu_login",
            "error",
            subject=request.username,
            reason="unexpected_error",
            auth_method="password",
            network_mode=request.network_mode,
            error_type=type(e).__name__,
        )
        return LoginResponse(
            success=False,
            message=f"登录错误（错误编号：{error_id}）",
            error_code="REQUEST_ERROR",
            suggestion="请稍后重试；若持续失败请查看日志。",
        )


@router.post("/api/webvpn/qr/start")
def start_webvpn_qr_login(request: WebVPNQRStartRequest):
    """Create an application-managed QR login session for WebVPN."""
    try:
        with remote_session_guard():
            previous = clear_pending_auth_client()
            if previous is not None:
                previous.cancel_webvpn_qr_login()
            active = peek_auth_client()
            account = request.username or str(getattr(active, "username", "") or "")
            client = NEUAuthClient(
                username=account,
                cookie_file=COOKIE_FILE,
                network_mode="webvpn",
                restore_session=False,
            )
            flow = client.start_webvpn_qr_login()
            set_pending_auth_client(client)
        log_security_event(
            "webvpn_qr_login",
            "pending",
            subject=request.username,
            auth_method="qr_start",
            network_mode="webvpn",
        )
        return {"success": True, **flow}
    except WebVPNLoginError as e:
        log_security_event(
            "webvpn_qr_login",
            "failure",
            subject=request.username,
            reason="qr_start_failed",
            auth_method="qr_start",
            network_mode="webvpn",
            error_type=type(e).__name__,
        )
        return _webvpn_failure(str(e), error_code=_error_code(e))
    except Exception as e:
        error_id = log_application_error("auth.webvpn_qr_start", e, 500)
        log_security_event(
            "webvpn_qr_login",
            "error",
            subject=request.username,
            reason="qr_start_failed",
            auth_method="qr_start",
            network_mode="webvpn",
            error_type=type(e).__name__,
        )
        return _webvpn_failure(
            f"无法启动 WebVPN 二维码登录（错误编号：{error_id}）",
            error_code=WEBVPN_ERR_UNKNOWN,
            status="error",
        )


@router.post("/api/webvpn/qr/status")
def get_webvpn_qr_status(request: WebVPNQRStatusRequest):
    """Poll a candidate WebVPN session and commit it only after success."""
    client = peek_pending_auth_client()
    if client is None:
        log_security_event(
            "webvpn_qr_login",
            "failure",
            reason="flow_missing",
            auth_method="qr",
            network_mode="webvpn",
        )
        return _webvpn_failure("二维码登录流程不存在", error_code=WEBVPN_ERR_FLOW_MISSING, status="missing")
    try:
        with remote_session_guard():
            if peek_pending_auth_client() is not client:
                log_security_event(
                    "webvpn_qr_login",
                    "failure",
                    reason="flow_replaced",
                    auth_method="qr",
                    network_mode="webvpn",
                )
                return _webvpn_failure(
                    "二维码登录流程不存在",
                    error_code=WEBVPN_ERR_FLOW_REPLACED,
                    status="missing",
                )
            result = client.poll_webvpn_qr_login(request.flow_id)
            if result.get("status") == "authenticated":
                clear_pending_auth_client(client)
                set_auth_client(client, force_epoch=True)
                schedule_login_bootstrap(client)
                log_security_event(
                    "webvpn_qr_login",
                    "success",
                    subject=getattr(client, "username", ""),
                    auth_method="qr",
                    network_mode="webvpn",
                )
            elif result.get("status") == "expired":
                clear_pending_auth_client(client)
        response = {"success": True, **result}
        if result.get("status") == "expired":
            response["error_code"] = WEBVPN_ERR_FLOW_EXPIRED
        return response
    except WebVPNLoginError as e:
        diagnostics = client.get_webvpn_qr_diagnostics()
        # A stale QR poll can race with QR -> SMS conversion.  Do not discard
        # the newer SMS challenge when the old QR flow has already been
        # replaced; only clear the candidate if no SMS flow is alive.
        if not getattr(client, "_webvpn_sms_flow", None):
            clear_pending_auth_client(client)
        log_security_event(
            "webvpn_qr_login",
            "failure",
            subject=getattr(client, "username", ""),
            reason="qr_callback_failed",
            auth_method="qr",
            network_mode="webvpn",
            error_type=type(e).__name__,
        )
        return _webvpn_failure(
            str(e),
            error_code=_error_code(e),
            diagnostics=diagnostics,
        )
    except Exception as e:
        clear_pending_auth_client(client)
        error_id = log_application_error("auth.webvpn_qr_poll", e, 500)
        log_security_event(
            "webvpn_qr_login",
            "error",
            subject=getattr(client, "username", ""),
            reason="qr_poll_failed",
            auth_method="qr",
            network_mode="webvpn",
            error_type=type(e).__name__,
        )
        return _webvpn_failure(
            f"二维码登录暂时失败（错误编号：{error_id}）",
            error_code=WEBVPN_ERR_UNKNOWN,
        )


@router.post("/api/webvpn/qr/cancel")
def cancel_webvpn_qr_login(request: WebVPNQRStatusRequest):
    client = peek_pending_auth_client()
    try:
        if client:
            with remote_session_guard():
                client.cancel_webvpn_qr_login(request.flow_id)
                clear_pending_auth_client(client)
        log_security_event("webvpn_qr_login", "success", auth_method="qr_cancel")
    except Exception as error:
        error_id = log_application_error("auth.webvpn_qr_cancel", error, 500)
        log_security_event(
            "webvpn_qr_login",
            "error",
            reason="qr_cancel_failed",
            auth_method="qr_cancel",
            error_type=type(error).__name__,
        )
        return _webvpn_failure(
            f"取消二维码登录失败（错误编号：{error_id}）",
            error_code=WEBVPN_ERR_UNKNOWN,
        )
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
                log_security_event(
                    "webvpn_password_login",
                    "success",
                    subject=request.username,
                    auth_method="password",
                    network_mode="webvpn",
                    remember=request.remember,
                )
            else:
                # The flow stays only in memory and is discarded on server restart.
                client._webvpn_sms_flow["remember"] = request.remember
                previous = clear_pending_auth_client()
                if previous is not None and previous is not client:
                    previous.cancel_webvpn_qr_login()
                    previous.cancel_webvpn_sms_login()
                set_pending_auth_client(client)
                log_security_event(
                    "webvpn_password_login",
                    "pending",
                    subject=request.username,
                    auth_method="password_sms_challenge",
                    network_mode="webvpn",
                    remember=request.remember,
                )
        return {"success": True, **result}
    except NEULoginError as error:
        error_code = (
            "WRONG_PASSWORD"
            if error.error_type == LOGIN_ERR_WRONG_PWD
            else (getattr(error, "error_code", None) or WEBVPN_ERR_UNKNOWN)
        )
        log_security_event(
            "webvpn_password_login",
            "failure",
            subject=request.username,
            reason="wrong_password" if error.error_type == LOGIN_ERR_WRONG_PWD else "request_error",
            auth_method="password",
            network_mode="webvpn",
            error_type=type(error).__name__,
        )
        return {
            "success": False, "message": str(error),
            "error_code": error_code,
            "suggestion": _webvpn_suggestion(error_code),
        }
    except Exception as error:
        error_id = log_application_error("auth.webvpn_password_login", error, 500)
        log_security_event(
            "webvpn_password_login",
            "error",
            subject=request.username,
            reason="unexpected_error",
            auth_method="password",
            network_mode="webvpn",
            error_type=type(error).__name__,
        )
        return {
            "success": False, "message": f"WebVPN 登录失败（错误编号：{error_id}）",
            "error_code": WEBVPN_ERR_UNKNOWN, "suggestion": "请检查网络或改用微信扫码快速登录。",
        }


@router.post("/api/webvpn/sms/send")
def send_webvpn_sms_code(request: WebVPNSMSSendRequest):
    client = _webvpn_sms_client(request.flow_id)
    if client is None:
        log_security_event(
            "webvpn_sms_send",
            "failure",
            reason="flow_missing",
            auth_method="sms",
        )
        return _webvpn_failure(
            "短信验证流程不存在，请重新登录",
            error_code=WEBVPN_ERR_FLOW_MISSING,
            status="missing",
        )
    try:
        with remote_session_guard():
            result = client.send_webvpn_sms_code(request.flow_id, request.captcha_code)
        if result.get("status") == "captcha_invalid":
            log_security_event("webvpn_sms_send", "failure", reason="captcha_invalid", auth_method="sms")
            return {
                "success": False,
                "captcha_invalid": True,
                "error_code": WEBVPN_ERR_CAPTCHA_INVALID,
                **result,
            }
        log_security_event("webvpn_sms_send", "success", auth_method="sms")
        return {"success": True, **result}
    except WebVPNLoginError as error:
        log_security_event(
            "webvpn_sms_send",
            "failure",
            reason="sms_send_failed",
            auth_method="sms",
            error_type=type(error).__name__,
        )
        return _webvpn_failure(str(error), error_code=_error_code(error))
    except Exception as error:
        error_id = log_application_error("auth.webvpn_sms_send", error, 500)
        log_security_event(
            "webvpn_sms_send",
            "error",
            reason="sms_send_failed",
            auth_method="sms",
            error_type=type(error).__name__,
        )
        return _webvpn_failure(
            f"发送短信验证码失败（错误编号：{error_id}）",
            error_code=WEBVPN_ERR_UNKNOWN,
        )


@router.post("/api/webvpn/sms/captcha/refresh")
def refresh_webvpn_captcha(request: WebVPNSMSCodeRequest):
    client = _webvpn_sms_client(request.flow_id)
    if client is None:
        return _webvpn_failure(
            "短信验证流程不存在，请重新登录",
            error_code=WEBVPN_ERR_FLOW_MISSING,
            status="missing",
        )
    try:
        with remote_session_guard():
            result = client.refresh_webvpn_captcha(request.flow_id)
        return {"success": True, **result}
    except WebVPNLoginError as error:
        return _webvpn_failure(str(error), error_code=_error_code(error))
    except Exception as error:
        error_id = log_application_error("auth.webvpn_captcha_refresh", error, 500)
        return _webvpn_failure(
            f"刷新图形验证码失败（错误编号：{error_id}）",
            error_code=WEBVPN_ERR_CAPTCHA_FETCH,
        )


@router.post("/api/webvpn/sms/verify")
def verify_webvpn_sms_code(request: WebVPNSMSVerifyRequest):
    client = _webvpn_sms_client(request.flow_id)
    if client is None:
        log_security_event(
            "webvpn_sms_verify",
            "failure",
            reason="flow_missing",
            auth_method="sms",
        )
        return _webvpn_failure(
            "短信验证流程不存在，请重新登录",
            error_code=WEBVPN_ERR_FLOW_MISSING,
            status="missing",
        )
    try:
        remember = bool((client._webvpn_sms_flow or {}).get("remember"))
        with remote_session_guard():
            if client is not peek_auth_client() and client is not peek_pending_auth_client():
                log_security_event(
                    "webvpn_sms_verify",
                    "failure",
                    reason="flow_replaced",
                    auth_method="sms",
                )
                return _webvpn_failure(
                    "短信验证流程不存在，请重新登录",
                    error_code=WEBVPN_ERR_FLOW_REPLACED,
                    status="missing",
                )
            result = client.verify_webvpn_sms_code(request.flow_id, request.code, request.trust_device)
            if result.get("status") == "authenticated":
                if peek_pending_auth_client() is client:
                    clear_pending_auth_client(client)
                _save_webvpn_password_login(client, remember)
        if result.get("status") != "authenticated":
            return {
                "success": False,
                "error_code": result.get("error_code", WEBVPN_ERR_CAPTCHA_INVALID),
                **result,
            }
        log_security_event(
            "webvpn_sms_verify",
            "success",
            subject=getattr(client, "username", ""),
            auth_method="sms",
            network_mode="webvpn",
            trust_device=request.trust_device,
            remember=remember,
        )
        return {"success": True, **result}
    except WebVPNLoginError as error:
        log_security_event(
            "webvpn_sms_verify",
            "failure",
            subject=getattr(client, "username", ""),
            reason="sms_verify_failed",
            auth_method="sms",
            error_type=type(error).__name__,
        )
        return _webvpn_failure(str(error), error_code=_error_code(error, WEBVPN_ERR_SMS_INVALID))
    except Exception as error:
        error_id = log_application_error("auth.webvpn_sms_verify", error, 500)
        log_security_event(
            "webvpn_sms_verify",
            "error",
            subject=getattr(client, "username", ""),
            reason="sms_verify_failed",
            auth_method="sms",
            error_type=type(error).__name__,
        )
        return _webvpn_failure(
            f"短信验证失败（错误编号：{error_id}）",
            error_code=WEBVPN_ERR_UNKNOWN,
        )


@router.post("/api/webvpn/sms/cancel")
def cancel_webvpn_sms_login(request: WebVPNSMSCodeRequest):
    client = _webvpn_sms_client(request.flow_id)
    try:
        if client:
            with remote_session_guard():
                client.cancel_webvpn_sms_login(request.flow_id)
                if peek_pending_auth_client() is client:
                    clear_pending_auth_client(client)
        log_security_event("webvpn_sms_verify", "success", auth_method="sms_cancel")
    except Exception as error:
        error_id = log_application_error("auth.webvpn_sms_cancel", error, 500)
        log_security_event(
            "webvpn_sms_verify",
            "error",
            reason="sms_cancel_failed",
            auth_method="sms_cancel",
            error_type=type(error).__name__,
        )
        return _webvpn_failure(
            f"取消短信验证失败（错误编号：{error_id}）",
            error_code=WEBVPN_ERR_UNKNOWN,
        )
    return {"success": True}


@router.post("/api/logout")
def logout(clear_data: bool = Query(True, description="是否清理用户数据")):
    """
    登出接口

    Args:
        clear_data: 是否清理用户数据（成绩、培养计划、头像等），默认 True
    """
    result = {"success": True, "message": "已登出"}

    # Linux/反向代理部署中，远端 Session 可能正被慢请求占用。登出必须
    # 先在远端队列中预留 mutation 位置，再立即撤销应用内身份；否则
    # “撤销身份”和“排队清理”之间的新登录可能抢先完成，随后又被旧
    # logout 清除。
    client = peek_auth_client()
    pending_client = peek_pending_auth_client()
    account = str(getattr(client, "username", "") or "") or None

    def fence_identity() -> None:
        logout_auth_client(clear_cache=clear_data)

    # 新登录看到 mutation 已排队后只能等待这次清理完成。正在执行的
    # 旧认证请求不会被强制中断；若它在早期 fence 后迟到提交身份，
    # 取得锁后再 fence 一次。
    with remote_session_guard(
        priority="mutation",
        label="logout",
        on_queued=fence_identity,
    ):
        late_client = peek_auth_client()
        late_pending_client = peek_pending_auth_client()
        if late_client is not None or late_pending_client is not None:
            # An authentication request that already held the Session lock may
            # have completed after the early fence. Revoke that result before
            # any newly submitted login is allowed to acquire the lock.
            logout_auth_client(clear_cache=clear_data)
        clients_to_clear = {
            id(candidate): candidate
            for candidate in (
                client,
                pending_client,
                late_client,
                late_pending_client,
            )
            if candidate is not None
        }
        for candidate in clients_to_clear.values():
            candidate.cancel_webvpn_qr_login()
            candidate.cancel_webvpn_sms_login()
            candidate.clear_cookies()
            candidate.session.cookies.clear()
            clear_pending_auth_client(candidate)
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
                error_id = log_application_error("auth.logout_cleanup", e, 500)
                result["data_cleared"] = False
                result["clear_error"] = f"清理失败（错误编号：{error_id}）"

    log_security_event(
        "neu_logout",
        "success",
        subject=account,
        clear_data=clear_data,
    )
    return result
