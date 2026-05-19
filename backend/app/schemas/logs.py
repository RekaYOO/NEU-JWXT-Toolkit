from typing import Optional, List, Dict, Any
from pydantic import BaseModel


class LogSummaryResponse(BaseModel):
    """日志摘要响应"""
    period_days: int
    total_files: int
    total_size_mb: float
    categories: Dict[str, Any]


class LogEntryResponse(BaseModel):
    """日志条目响应"""
    timestamp: str
    level: str
    logger: str
    message: str


class LogListResponse(BaseModel):
    """日志列表响应"""
    category: str
    date: str
    filename: str
    size_mb: float
