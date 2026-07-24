"""Runtime health, access gateway, and desktop lifecycle routes."""

from __future__ import annotations

import os
import threading

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from backend.core.runtime import get_runtime_config
from backend.core.runtime.access import (
    COOKIE_NAME,
    COOKIE_TTL_SECONDS,
    LoginRateLimiter,
    issue_access_cookie,
    request_source,
    request_uses_https,
    validate_access_cookie,
    verify_access_password,
)


router = APIRouter()
config = get_runtime_config()
rate_limiter = LoginRateLimiter()


class AccessLoginRequest(BaseModel):
    password: str


@router.get("/api/health")
async def health():
    return {
        "status": "ok",
        "version": config.version,
        "profile": config.profile,
    }


@router.get("/api/access/status")
async def access_status(request: Request):
    if not config.access_gateway_enabled:
        return {"required": False, "authenticated": True, "configured": True}
    configured = bool(
        config.access_password_salt
        and config.access_password_hash
        and config.session_secret
    )
    token = request.cookies.get(COOKIE_NAME, "")
    return {
        "required": True,
        "configured": configured,
        "authenticated": configured
        and validate_access_cookie(token, config.session_secret),
    }


@router.post("/api/access/login")
async def access_login(payload: AccessLoginRequest, request: Request, response: Response):
    if not config.access_gateway_enabled:
        return {"success": True}
    if not (
        config.access_password_salt
        and config.access_password_hash
        and config.session_secret
    ):
        raise HTTPException(status_code=503, detail="服务器访问密码尚未配置")

    source = request_source(request, config)
    if rate_limiter.is_blocked(source):
        raise HTTPException(status_code=429, detail="失败次数过多，请 5 分钟后重试")
    if not verify_access_password(
        payload.password,
        config.access_password_salt,
        config.access_password_hash,
    ):
        rate_limiter.register_failure(source)
        raise HTTPException(status_code=401, detail="访问密码错误")

    rate_limiter.clear(source)
    response.set_cookie(
        COOKIE_NAME,
        issue_access_cookie(config.session_secret),
        max_age=COOKIE_TTL_SECONDS,
        httponly=True,
        secure=request_uses_https(request, config),
        samesite="lax",
        path="/",
    )
    return {"success": True}


@router.post("/api/access/logout")
async def access_logout(response: Response):
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"success": True}


@router.post("/api/runtime/shutdown")
async def desktop_shutdown():
    if not config.desktop_mode:
        raise HTTPException(status_code=404, detail="该运行模式不支持关闭服务")
    threading.Timer(0.25, lambda: os._exit(0)).start()
    return {"success": True}
