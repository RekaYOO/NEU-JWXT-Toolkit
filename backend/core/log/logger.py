"""
neu_log/logger.py
=================
核心日志管理器

支持：
- 分级日志（DEBUG/INFO/WARNING/ERROR/CRITICAL）
- 分类存储（system/access/error）
- 按日期自动轮转
- 结构化日志输出（JSON）
"""

import json
import logging
import logging.handlers
import os
import sys
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional, Dict, Any, Union


class LogLevel(Enum):
    """日志级别"""
    DEBUG = logging.DEBUG
    INFO = logging.INFO
    WARNING = logging.WARNING
    ERROR = logging.ERROR
    CRITICAL = logging.CRITICAL


class LogCategory(Enum):
    """日志分类"""
    SYSTEM = "system"      # 系统日志
    ACCESS = "access"      # 访问日志（API请求）
    ERROR = "error"        # 错误日志
    LOGIN = "login"        # 登录相关
    SYNC = "sync"          # 数据同步


class LogConfig:
    """日志配置"""
    
    def __init__(
        self,
        log_dir: str = "",
        level: LogLevel = LogLevel.INFO,
        max_bytes: int = 10 * 1024 * 1024,  # 10MB
        backup_count: int = 7,               # 保留7个备份
        format_type: str = "text",           # text 或 json
        console_output: bool = True,         # 是否输出到控制台
    ):
        self.log_dir = log_dir or os.path.join(os.getcwd(), "data", "logs")
        self.level = level
        self.max_bytes = max_bytes
        self.backup_count = backup_count
        self.format_type = format_type
        self.console_output = console_output
        
        # 确保日志目录存在
        Path(self.log_dir).mkdir(parents=True, exist_ok=True)
    
    def get_log_path(self, category: LogCategory, date: Optional[str] = None) -> str:
        """获取日志文件路径"""
        if date is None:
            date = datetime.now().strftime("%Y-%m-%d")
        return os.path.join(self.log_dir, f"{category.value}_{date}.log")


# 全局配置
_default_config: Optional[LogConfig] = None
_loggers: Dict[str, logging.Logger] = {}


def setup_logging(config: Optional[LogConfig] = None) -> LogConfig:
    """
    初始化日志系统
    
    Args:
        config: 日志配置，None则使用默认
        
    Returns:
        LogConfig 实例
    """
    global _default_config
    _default_config = config or LogConfig()
    
    # 配置根日志器
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    
    # 清除现有处理器
    root_logger.handlers.clear()
    
    # 控制台输出
    if _default_config.console_output:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(_default_config.level.value)
        console_formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        console_handler.setFormatter(console_formatter)
        root_logger.addHandler(console_handler)
    
    return _default_config


def get_logger(
    name: str,
    category: LogCategory = LogCategory.SYSTEM,
    config: Optional[LogConfig] = None
) -> logging.Logger:
    """
    获取日志记录器
    
    Args:
        name: 日志器名称
        category: 日志分类
        config: 日志配置
        
    Returns:
        Logger 实例
    """
    global _default_config, _loggers
    
    if config is None:
        config = _default_config or LogConfig()
    
    cache_key = f"{category.value}:{name}"
    if cache_key in _loggers:
        return _loggers[cache_key]
    
    # 创建日志器
    logger = logging.getLogger(f"neu.{category.value}.{name}")
    logger.setLevel(logging.DEBUG)
    
    # 避免重复添加处理器
    if logger.handlers:
        return logger
    
    # 文件处理器 - 按分类分文件
    log_path = config.get_log_path(category)
    file_handler = logging.handlers.TimedRotatingFileHandler(
        log_path,
        when='midnight',
        interval=1,
        backupCount=config.backup_count,
        encoding='utf-8'
    )
    file_handler.setLevel(logging.DEBUG)
    
    # 格式化
    if config.format_type == "json":
        formatter = JsonFormatter()
    else:
        formatter = logging.Formatter(
            '%(asctime)s [%(levelname)s] %(name)s: %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
    
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    
    # 错误日志同时写入 error 文件
    if category != LogCategory.ERROR:
        error_handler = logging.handlers.TimedRotatingFileHandler(
            config.get_log_path(LogCategory.ERROR),
            when='midnight',
            interval=1,
            backupCount=config.backup_count,
            encoding='utf-8'
        )
        error_handler.setLevel(logging.ERROR)
        error_handler.setFormatter(formatter)
        logger.addHandler(error_handler)
    
    _loggers[cache_key] = logger
    return logger


class JsonFormatter(logging.Formatter):
    """JSON 格式日志格式化器"""
    
    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            "timestamp": datetime.fromtimestamp(record.created).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        
        # 添加额外字段
        if hasattr(record, 'extra'):
            log_data.update(record.extra)
        
        # 异常信息
        if record.exc_info:
            log_data['exception'] = self.formatException(record.exc_info)
        
        return json.dumps(log_data, ensure_ascii=False)


class StructuredLogger:
    """
    结构化日志记录器
    
    支持上下文信息自动附加
    """
    
    def __init__(self, name: str, category: LogCategory = LogCategory.SYSTEM):
        self.name = name
        self.category = category
        self._context: Dict[str, Any] = {}
        self._logger = get_logger(name, category)
    
    def with_context(self, **kwargs) -> 'StructuredLogger':
        """添加上下文信息"""
        self._context.update(kwargs)
        return self
    
    def _log(self, level: int, msg: str, extra: Optional[Dict] = None):
        """内部日志方法"""
        merged_extra = {**self._context}
        if extra:
            merged_extra.update(extra)
        
        if merged_extra:
            self._logger.log(level, msg, extra={'extra': merged_extra})
        else:
            self._logger.log(level, msg)
    
    def debug(self, msg: str, **kwargs):
        self._log(logging.DEBUG, msg, kwargs)
    
    def info(self, msg: str, **kwargs):
        self._log(logging.INFO, msg, kwargs)
    
    def warning(self, msg: str, **kwargs):
        self._log(logging.WARNING, msg, kwargs)
    
    def error(self, msg: str, **kwargs):
        self._log(logging.ERROR, msg, kwargs)
    
    def exception(self, msg: str, **kwargs):
        self._log(logging.ERROR, msg, kwargs)
        self._logger.exception(msg)


# 便捷函数
def get_system_logger(name: str) -> logging.Logger:
    """获取系统日志记录器"""
    return get_logger(name, LogCategory.SYSTEM)


def get_access_logger(name: str) -> logging.Logger:
    """获取访问日志记录器"""
    return get_logger(name, LogCategory.ACCESS)


def get_login_logger(name: str) -> logging.Logger:
    """获取登录日志记录器"""
    return get_logger(name, LogCategory.LOGIN)
