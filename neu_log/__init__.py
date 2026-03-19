"""
neu_log - NEU 教务系统日志模块

提供统一的日志记录功能：
- 按日期轮转
- 分级存储（系统/访问/错误）
- 支持 API 查看和下载
"""

from .logger import (
    get_logger,
    setup_logging,
    LogConfig,
    LogLevel,
    LogCategory,
)

from .access_logger import (
    AccessLogger,
    log_api_call,
)

__all__ = [
    'get_logger',
    'setup_logging',
    'LogConfig',
    'LogLevel',
    'LogCategory',
    'AccessLogger',
    'log_api_call',
]
