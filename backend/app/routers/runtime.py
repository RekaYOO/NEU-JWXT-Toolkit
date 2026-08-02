"""Runtime health, access gateway, and desktop lifecycle routes."""

from __future__ import annotations

import os
import hmac
import threading
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from backend.core.runtime import get_runtime_config
from backend.core.runtime.access import (
    COOKIE_NAME,
    COOKIE_TTL_SECONDS,
    LoginRateLimiter,
    MAX_PASSWORD_LENGTH,
    access_cookie_payload,
    issue_access_cookie,
    request_source,
    request_uses_https,
    validate_access_cookie,
    verify_access_password,
)
from backend.core.log import log_security_event
from backend.core.log.access_logger import update_request_context


router = APIRouter()
config = get_runtime_config()
rate_limiter = LoginRateLimiter()


class AccessLoginRequest(BaseModel):
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)


def _access_gateway_configured() -> bool:
    return bool(
        config.access_password_salt
        and config.access_password_hash
        and len(config.session_secret) >= 32
    )


@router.get("/api/health")
async def health():
    result = {
        "status": "ok",
        "version": config.version,
        "profile": config.profile,
    }
    if config.desktop_mode:
        result["shutdown_token"] = os.environ.get("NEU_JWXT_SHUTDOWN_TOKEN", "")
    return result


@router.get("/api/access/status")
async def access_status(request: Request):
    if not config.access_gateway_enabled:
        return {"required": False, "authenticated": True, "configured": True}
    configured = _access_gateway_configured()
    token = request.cookies.get(COOKIE_NAME, "")
    return {
        "required": True,
        "configured": configured,
        "authenticated": configured
        and validate_access_cookie(token, config.session_secret),
    }


@router.post("/api/access/login")
async def access_login(payload: AccessLoginRequest, request: Request, response: Response):
    source = request_source(request, config)
    update_request_context(
        client_ip=source,
        peer_ip=request.client.host if request.client else "unknown",
    )
    if not config.access_gateway_enabled:
        log_security_event(
            "access_gateway_login",
            "success",
            auth_method="gateway_disabled",
        )
        return {"success": True}
    if not _access_gateway_configured():
        log_security_event(
            "access_gateway_login",
            "error",
            reason="gateway_not_configured",
        )
        raise HTTPException(status_code=503, detail="服务器访问密码尚未配置")

    if rate_limiter.is_blocked(source):
        log_security_event(
            "access_gateway_login",
            "blocked",
            reason="rate_limit",
        )
        raise HTTPException(status_code=429, detail="失败次数过多，请 5 分钟后重试")
    if not verify_access_password(
        payload.password,
        config.access_password_salt,
        config.access_password_hash,
    ):
        rate_limiter.register_failure(source)
        log_security_event(
            "access_gateway_login",
            "failure",
            reason="wrong_password",
        )
        raise HTTPException(status_code=401, detail="访问密码错误")

    rate_limiter.clear(source)
    access_token = issue_access_cookie(config.session_secret)
    response.set_cookie(
        COOKIE_NAME,
        access_token,
        max_age=COOKIE_TTL_SECONDS,
        httponly=True,
        secure=request_uses_https(request, config),
        samesite="lax",
        path="/",
    )
    cookie_payload = access_cookie_payload(access_token, config.session_secret)
    update_request_context(
        access_session_id=cookie_payload.get("session_id") if cookie_payload else None,
    )
    log_security_event("access_gateway_login", "success", auth_method="password")
    return {"success": True}


@router.post("/api/access/logout")
async def access_logout(response: Response):
    response.delete_cookie(COOKIE_NAME, path="/")
    log_security_event("access_gateway_logout", "success")
    return {"success": True}


@router.post("/api/runtime/shutdown")
async def desktop_shutdown(request: Request):
    if not config.desktop_mode:
        raise HTTPException(status_code=404, detail="该运行模式不支持关闭服务")
    expected_host = f"127.0.0.1:{config.port}"
    supplied_token = request.headers.get("x-neu-shutdown-token", "")
    expected_token = os.environ.get("NEU_JWXT_SHUTDOWN_TOKEN", "")
    if (
        request.headers.get("host", "") != expected_host
        or not expected_token
        or not hmac.compare_digest(supplied_token, expected_token)
    ):
        raise HTTPException(status_code=403, detail="关闭请求验证失败")
    origin = request.headers.get("origin")
    if origin:
        parsed_origin = urlsplit(origin)
        if (
            parsed_origin.scheme != "http"
            or parsed_origin.netloc != expected_host
            or parsed_origin.path not in {"", "/"}
            or parsed_origin.query
            or parsed_origin.fragment
        ):
            raise HTTPException(status_code=403, detail="关闭请求来源无效")
    shutdown = getattr(request.app.state, "desktop_shutdown", None)
    if not callable(shutdown):
        raise HTTPException(status_code=503, detail="桌面服务尚未准备好关闭")
    threading.Timer(0.1, shutdown).start()
    return {"success": True}
