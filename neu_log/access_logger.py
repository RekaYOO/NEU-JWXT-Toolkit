"""
neu_log/access_logger.py
========================
API 访问日志记录

记录所有 API 请求和响应信息
"""

import json
import time
import uuid
from datetime import datetime
from typing import Optional, Dict, Any, Callable
from functools import wraps

from .logger import get_logger, LogCategory, LogConfig


class AccessLogger:
    """访问日志记录器"""
    
    def __init__(self, config: Optional[LogConfig] = None):
        self.logger = get_logger("access", LogCategory.ACCESS, config)
        self.config = config or LogConfig()
    
    def log_request(
        self,
        method: str,
        path: str,
        client_ip: str,
        user_agent: str,
        status_code: int,
        response_time_ms: float,
        user_id: Optional[str] = None,
        request_id: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None
    ):
        """
        记录 API 访问日志
        
        Args:
            method: HTTP 方法
            path: 请求路径
            client_ip: 客户端 IP
            user_agent: User-Agent
            status_code: HTTP 状态码
            response_time_ms: 响应时间（毫秒）
            user_id: 用户 ID
            request_id: 请求 ID
            extra: 额外信息
        """
        log_data = {
            "timestamp": datetime.now().isoformat(),
            "request_id": request_id or str(uuid.uuid4())[:8],
            "method": method,
            "path": path,
            "client_ip": client_ip,
            "user_agent": user_agent,
            "status_code": status_code,
            "response_time_ms": round(response_time_ms, 2),
            "user_id": user_id,
        }
        
        if extra:
            log_data.update(extra)
        
        # 根据状态码选择日志级别
        if status_code >= 500:
            self.logger.error(f"API {method} {path} {status_code} {response_time_ms:.2f}ms")
        elif status_code >= 400:
            self.logger.warning(f"API {method} {path} {status_code} {response_time_ms:.2f}ms")
        else:
            self.logger.info(f"API {method} {path} {status_code} {response_time_ms:.2f}ms")
        
        # JSON 格式详细记录
        if self.config.format_type == "json":
            self.logger.debug(json.dumps(log_data, ensure_ascii=False))


# 请求上下文存储
_request_context: Dict[str, Any] = {}


def set_request_context(**kwargs):
    """设置当前请求的上下文信息"""
    import threading
    thread_id = threading.current_thread().ident
    if thread_id not in _request_context:
        _request_context[thread_id] = {}
    _request_context[thread_id].update(kwargs)


def get_request_context() -> Dict[str, Any]:
    """获取当前请求的上下文信息"""
    import threading
    thread_id = threading.current_thread().ident
    return _request_context.get(thread_id, {})


def clear_request_context():
    """清除当前请求的上下文信息"""
    import threading
    thread_id = threading.current_thread().ident
    _request_context.pop(thread_id, None)


def log_api_call(func: Callable) -> Callable:
    """
    装饰器：自动记录 API 调用日志
    
    用于记录函数调用信息
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        start_time = time.time()
        logger = get_logger("api_call", LogCategory.ACCESS)
        
        func_name = func.__qualname__
        logger.info(f"调用开始: {func_name}")
        
        try:
            result = func(*args, **kwargs)
            elapsed = (time.time() - start_time) * 1000
            logger.info(f"调用成功: {func_name}, 耗时 {elapsed:.2f}ms")
            return result
        except Exception as e:
            elapsed = (time.time() - start_time) * 1000
            logger.error(f"调用失败: {func_name}, 错误: {e}, 耗时 {elapsed:.2f}ms")
            raise
    
    return wrapper


class FastAPILogMiddleware:
    """
    FastAPI 日志中间件
    
    用法:
        from fastapi import FastAPI
        from neu_log import FastAPILogMiddleware
        
        app = FastAPI()
        app.add_middleware(FastAPILogMiddleware)
    """
    
    def __init__(self, app, config: Optional[LogConfig] = None):
        self.app = app
        self.access_logger = AccessLogger(config)
        self.system_logger = get_logger("middleware", LogCategory.SYSTEM, config)
    
    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        
        start_time = time.time()
        request_id = str(uuid.uuid4())[:8]
        
        # 提取请求信息
        method = scope.get("method", "")
        path = scope.get("path", "")
        headers = dict(scope.get("headers", []))
        
        client_ip = scope.get("client", ("unknown", 0))[0]
        user_agent = headers.get(b"user-agent", b"").decode("utf-8", errors="ignore")
        
        # 存储请求 ID 到上下文
        set_request_context(request_id=request_id)
        
        # 包装 send 以捕获响应状态
        status_code = 200
        
        async def wrapped_send(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message.get("status", 200)
            await send(message)
        
        try:
            await self.app(scope, receive, wrapped_send)
        except Exception as e:
            self.system_logger.error(f"请求处理异常: {method} {path}, 错误: {e}")
            raise
        finally:
            # 记录访问日志
            elapsed = (time.time() - start_time) * 1000
            self.access_logger.log_request(
                method=method,
                path=path,
                client_ip=client_ip,
                user_agent=user_agent,
                status_code=status_code,
                response_time_ms=elapsed,
                request_id=request_id,
            )
            clear_request_context()
