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
