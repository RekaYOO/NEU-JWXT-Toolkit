"""
全局状态管理与依赖注入

所有全局单例状态集中在此，供 routers 通过 FastAPI Depends 使用。
"""

import os
from typing import Optional

from backend.core.auth import NEUAuthClient
from backend.core.storage import Storage, AcademicStorage, AutoLoginManager, AcademicReportStorage
from backend.core.log import setup_logging, LogConfig, LogLevel, LogCategory, get_logger
from backend.core.log.manager import LogManager

# ── 全局状态 ──────────────────────────────────────────────────────────────────

_auth_client: Optional[NEUAuthClient] = None
_storage = Storage()
_academic_storage = AcademicStorage(_storage)

# Cookie 持久化文件路径
COOKIE_FILE = os.path.join(_storage.config.data_dir, "session.pkl")

# 初始化日志系统
_log_config = LogConfig(
    log_dir=os.path.join(_storage.config.data_dir, "logs"),
    level=LogLevel.INFO,
    console_output=True,
)
setup_logging(_log_config)

# 初始化自动登录管理器
_auto_login = AutoLoginManager(_storage, cookie_file=COOKIE_FILE)

# 初始化日志管理器
_log_manager = LogManager(_log_config)

# API 错误日志记录器
_api_logger = get_logger("api", LogCategory.SYSTEM, _log_config)

# 初始化培养计划存储
_report_storage = AcademicReportStorage(_storage)


# ── 全局状态修改接口 ──────────────────────────────────────────────────────────

def set_auth_client(client: Optional[NEUAuthClient]):
    """设置当前认证客户端"""
    global _auth_client
    _auth_client = client


# ── 依赖函数 ──────────────────────────────────────────────────────────────────

def get_auth_client() -> Optional[NEUAuthClient]:
    """
    获取当前认证客户端

    恢复优先级：
    1. 内存中的客户端（如果有效）
    2. 尝试用保存的 Cookie 恢复（免密）
    3. 尝试用保存的密码重新登录
    """
    global _auth_client

    # 1. 检查内存中的客户端
    if _auth_client is not None:
        # 尝试确保登录（内部会优先用 Cookie 刷新）
        if _auth_client.ensure_login():
            return _auth_client

    # 2. 尝试加载保存的凭证并创建客户端
    creds = _storage.load_credentials()
    if creds:
        username, password = creds
        # 创建客户端时会自动尝试从 Cookie 文件恢复
        client = NEUAuthClient(
            username=username,
            password=password,
            cookie_file=COOKIE_FILE
        )
        # 尝试登录（内部会优先用 Cookie 刷新票据）
        if client.ensure_login():
            _auth_client = client
            return _auth_client

    return None


def require_auth() -> NEUAuthClient:
    """需要登录的依赖"""
    client = get_auth_client()
    if client is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=401, detail="未登录或登录已过期")
    return client


# ── GPA 模拟工具函数 ──────────────────────────────────────────────────────────

def get_gpa_simulation_dir():
    """获取GPA模拟文件存储目录"""
    sim_dir = os.path.join(_storage.config.data_dir, "成绩")
    os.makedirs(sim_dir, exist_ok=True)
    return sim_dir
