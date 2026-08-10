"""
backend/app/main.py
==================
FastAPI 后端服务入口

教务系统工具箱 API
- 仅负责应用初始化、中间件配置和路由挂载
- 具体业务逻辑见 app/routers/ 下的各模块
"""

import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

# 确保项目根目录在 PYTHONPATH 中，以支持 backend.* 绝对导入
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.responses import FileResponse

from backend.core.log.access_logger import FastAPILogMiddleware
from backend.app.dependencies import (
    get_application_services,
    get_log_config,
    peek_auth_client,
)
from backend.app.routers import auth, cache, scores, logs, report, experiment, user, gpa, evaluation, exam, offline, research, runtime, tracking, festival_activities, course_selection, timetable, scheduling, course_outline
from backend.core.runtime import get_runtime_config, resource_path
from backend.core.runtime.access import AccessGatewayMiddleware

runtime_config = get_runtime_config()
application_services = get_application_services()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    application_services.start()
    try:
        yield
    finally:
        application_services.shutdown(timeout=8)


# ── FastAPI 应用 ───────────────────────────────────────────────────────────────

app = FastAPI(
    title="NEU教务系统工具箱 API",
    description="东北大学教务系统工具箱后端服务",
    version=runtime_config.version,
    docs_url="/docs" if runtime_config.profile == "development" else None,
    redoc_url="/redoc" if runtime_config.profile == "development" else None,
    openapi_url="/openapi.json" if runtime_config.profile == "development" else None,
    lifespan=lifespan,
)

def _current_account() -> str | None:
    client = peek_auth_client()
    if client is None or not getattr(client, "is_logged_in", False):
        return None
    return str(getattr(client, "username", "") or "") or None


# Starlette 后添加的中间件位于外层。网关先注册，确保日志层可以记录网关拒绝的 401。
app.add_middleware(AccessGatewayMiddleware, config=runtime_config)

# CORS 配置
if runtime_config.profile == "development":
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^http://(?:localhost|127\.0\.0\.1):\d+$",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.add_middleware(
    FastAPILogMiddleware,
    config=get_log_config(),
    runtime_config=runtime_config,
    user_provider=_current_account,
)

# ── 路由挂载 ───────────────────────────────────────────────────────────────────

app.include_router(auth.router)
app.include_router(cache.router, prefix="/api")
app.include_router(scores.router, prefix="/api")
app.include_router(logs.router, prefix="/api")
app.include_router(report.router, prefix="/api")
app.include_router(experiment.router, prefix="/api")
app.include_router(user.router, prefix="/api")
app.include_router(gpa.router, prefix="/api")
app.include_router(evaluation.router, prefix="/api")
app.include_router(exam.router, prefix="/api")
app.include_router(offline.router, prefix="/api/offline")
app.include_router(research.router, prefix="/api")
app.include_router(festival_activities.router, prefix="/api")
app.include_router(course_selection.router, prefix="/api")
app.include_router(timetable.router, prefix="/api")
app.include_router(scheduling.router, prefix="/api")
app.include_router(course_outline.router, prefix="/api")
app.include_router(tracking.router, prefix="/api/grade-tracking")
app.include_router(runtime.router)

# ── 前端静态文件（生产/本地单端口模式）──────────────────────────────────────────

_FRONTEND_BUILD_DIR = resource_path("frontend", "build").resolve()
_FRONTEND_INDEX = _FRONTEND_BUILD_DIR / "index.html"
_FRONTEND_STATIC = _FRONTEND_BUILD_DIR / "static"
_FRONTEND_ROOT_FILES = frozenset(
    {
        "apple-touch-icon.png",
        "asset-manifest.json",
        "favicon.ico",
        "icon-192.png",
        "icon-512.png",
        "manifest.webmanifest",
    }
)


def _frontend_root_file(full_path: str) -> Path | None:
    """Return a safe, generated file from the frontend build root.

    CRA emits icons and its web manifest beside ``index.html``.  Nested assets
    are served by ``StaticFiles``; accepting a nested path here would turn the
    SPA fallback into a filesystem browser on both POSIX and Windows.
    """
    if (
        full_path not in _FRONTEND_ROOT_FILES
        or "/" in full_path
        or "\\" in full_path
    ):
        return None
    try:
        target = (_FRONTEND_BUILD_DIR / full_path).resolve()
        target.relative_to(_FRONTEND_BUILD_DIR)
    except (OSError, ValueError):
        return None
    return target if target.is_file() else None


def _has_unsafe_spa_path(full_path: str) -> bool:
    """Reject traversal syntax instead of disguising it as an SPA route."""
    normalized = full_path.replace("\\", "/")
    return "\x00" in full_path or any(
        segment in {".", ".."} for segment in normalized.split("/")
    )


if _FRONTEND_INDEX.is_file() and _FRONTEND_STATIC.is_dir():
    # 挂载静态资源目录
    app.mount("/static", StaticFiles(directory=_FRONTEND_STATIC), name="static")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        """SPA fallback：非 API 路由都返回 index.html"""
        # API 路由已在上方注册，不会走到这里
        if _has_unsafe_spa_path(full_path):
            raise HTTPException(status_code=404, detail="Not Found")
        # CRA 生成在 build 根目录的图标和 manifest 继续按原路径提供。
        # 只允许根级单文件；嵌套静态资源由上方 StaticFiles 安全处理。
        target = _frontend_root_file(full_path)
        if target is not None:
            return FileResponse(target)
        return FileResponse(_FRONTEND_INDEX)

# ── 启动 ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    # 从环境变量读取端口，默认为 8000
    port = runtime_config.port
    host = runtime_config.host

    print(f"启动 NEU 教务系统工具箱 API 服务...")
    print(f"监听地址: http://{host}:{port}")
    print(f"API 文档: http://{host}:{port}/docs")

    uvicorn.run(app, host=host, port=port, access_log=False)
