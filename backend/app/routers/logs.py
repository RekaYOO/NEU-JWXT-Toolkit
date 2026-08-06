from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from backend.app.dependencies import get_log_manager
from backend.app.schemas import LogSummaryResponse
from backend.core.log import LogCategory

router = APIRouter()


def _serialize_entry(entry):
    return {
        "timestamp": entry.timestamp,
        "level": entry.level,
        "logger": entry.logger,
        "message": entry.message,
        "event_type": entry.event_type,
        "event_title": entry.event_title,
        "summary": entry.summary,
        "details": entry.details or {},
        "structured": entry.structured,
    }


@router.get("/logs/summary", response_model=LogSummaryResponse)
def get_logs_summary(
    days: int = Query(7, ge=1, le=30),
    manager=Depends(get_log_manager),
):
    """获取日志统计摘要"""
    return manager.get_log_summary(days)


@router.get("/logs/files")
def get_log_files(
    category: Optional[str] = Query(None, description="日志分类: system/access/error/login/sync"),
    days: int = Query(7, ge=1, le=30),
    manager=Depends(get_log_manager),
):
    """获取日志文件列表"""
    cat = LogCategory(category) if category else None
    files = manager.get_log_files(category=cat, days=days)
    return [
        {
            "category": f.category,
            "date": f.date,
            "filename": f.filename,
            "size_mb": round(f.size_bytes / 1024 / 1024, 2),
            "modified_time": f.modified_time.isoformat(),
        }
        for f in files
    ]


@router.get("/logs/content")
def get_log_content(
    category: str = Query(..., description="日志分类"),
    date: str = Query(..., description="日期 (YYYY-MM-DD)"),
    level: Optional[str] = Query(None, description="日志级别过滤"),
    search: Optional[str] = Query(None, description="搜索关键词"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    manager=Depends(get_log_manager),
):
    """获取日志内容"""
    try:
        cat = LogCategory(category)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"无效的日志分类: {category}")

    entries = manager.read_log(cat, date, level=level, search=search, limit=limit, offset=offset)
    return {
        "category": category,
        "date": date,
        "total_returned": len(entries),
        "entries": [_serialize_entry(entry) for entry in entries]
    }


@router.get("/logs/tail")
def tail_log(
    category: str = Query(..., description="日志分类"),
    date: str = Query(..., description="日期 (YYYY-MM-DD)"),
    lines: int = Query(100, ge=1, le=500),
    manager=Depends(get_log_manager),
):
    """获取日志末尾 N 行"""
    try:
        cat = LogCategory(category)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"无效的日志分类: {category}")

    entries = manager.tail_log(cat, date, lines)
    return {
        "category": category,
        "date": date,
        "lines": len(entries),
        "entries": [_serialize_entry(entry) for entry in entries]
    }


@router.get("/logs/search")
def search_logs(
    keyword: str = Query(..., description="搜索关键词"),
    category: Optional[str] = Query(None, description="日志分类过滤"),
    days: int = Query(7, ge=1, le=30),
    limit: int = Query(100, ge=1, le=500),
    manager=Depends(get_log_manager),
):
    """搜索日志"""
    cat = LogCategory(category) if category else None
    results = manager.search_logs(keyword, cat, days, limit)
    return {"keyword": keyword, "total": len(results), "results": results}


@router.get("/logs/download/{category}/{date}")
def download_log(category: str, date: str, manager=Depends(get_log_manager)):
    """下载日志文件"""
    try:
        cat = LogCategory(category)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"无效的日志分类: {category}")

    content = manager.download_log(cat, date)
    if content is None:
        raise HTTPException(status_code=404, detail="日志文件不存在")

    filename = f"{category}_{date}.log"
    return Response(
        content=content,
        media_type="text/plain",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@router.delete("/logs/cleanup")
def cleanup_logs(
    keep_days: int = Query(30, ge=7, le=365),
    manager=Depends(get_log_manager),
):
    """清理旧日志"""
    deleted = manager.clear_old_logs(keep_days)
    return {"deleted_files": deleted, "keep_days": keep_days}
