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

# 确保项目根目录在 PYTHONPATH 中，以支持 backend.* 绝对导入
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.responses import FileResponse

from backend.core.log.access_logger import FastAPILogMiddleware
from backend.app.dependencies import _log_config
from backend.app.routers import auth, scores, logs, report, experiment, user, gpa, evaluation

# ── FastAPI 应用 ───────────────────────────────────────────────────────────────

app = FastAPI(
    title="NEU教务系统工具箱 API",
    description="东北大学教务系统工具箱后端服务",
    version="1.2.0"
)

# 日志中间件（必须在 CORS 之前）
app.add_middleware(FastAPILogMiddleware, config=_log_config)

# CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── 路由挂载 ───────────────────────────────────────────────────────────────────

app.include_router(auth.router)
app.include_router(scores.router, prefix="/api")
app.include_router(logs.router, prefix="/api")
app.include_router(report.router, prefix="/api")
app.include_router(experiment.router, prefix="/api")
app.include_router(user.router, prefix="/api")
app.include_router(gpa.router, prefix="/api")
app.include_router(evaluation.router, prefix="/api")

# ── 前端静态文件（生产/本地单端口模式）──────────────────────────────────────────

_FRONTEND_BUILD_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "frontend", "build"
)

if os.path.isdir(_FRONTEND_BUILD_DIR):
    # 挂载静态资源目录
    app.mount("/static", StaticFiles(directory=os.path.join(_FRONTEND_BUILD_DIR, "static")), name="static")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        """SPA fallback：非 API 路由都返回 index.html"""
        # API 路由已在上方注册，不会走到这里
        # 文件系统存在的静态文件（如 favicon.ico）优先返回
        target = os.path.join(_FRONTEND_BUILD_DIR, full_path)
        if os.path.isfile(target):
            return FileResponse(target)
        return FileResponse(os.path.join(_FRONTEND_BUILD_DIR, "index.html"))

# ── 启动 ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    # 从环境变量读取端口，默认为 8000
    port = int(os.environ.get("PORT", os.environ.get("BACKEND_PORT", "8000")))
    host = os.environ.get("HOST", "0.0.0.0")

    print(f"启动 NEU 教务系统工具箱 API 服务...")
    print(f"监听地址: http://{host}:{port}")
    print(f"API 文档: http://{host}:{port}/docs")

    uvicorn.run(app, host=host, port=port)
