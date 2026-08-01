from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


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
    event_type: str = "generic_system"
    event_title: str = "系统记录"
    summary: str = ""
    details: Dict[str, Any] = Field(default_factory=dict)
    structured: bool = False


class LogListResponse(BaseModel):
    """日志列表响应"""
    category: str
    date: str
    filename: str
    size_mb: float
