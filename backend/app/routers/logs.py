from typing import Optional
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from backend.app.dependencies import _log_manager, _log_config
from backend.app.schemas import LogSummaryResponse
from backend.core.log import LogCategory

router = APIRouter()


@router.get("/logs/summary", response_model=LogSummaryResponse)
async def get_logs_summary(days: int = Query(7, ge=1, le=30)):
    """获取日志统计摘要"""
    return _log_manager.get_log_summary(days)


@router.get("/logs/files")
async def get_log_files(
    category: Optional[str] = Query(None, description="日志分类: system/access/error/login/sync"),
    days: int = Query(7, ge=1, le=30)
):
    """获取日志文件列表"""
    cat = LogCategory(category) if category else None
    files = _log_manager.get_log_files(category=cat, days=days)
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
async def get_log_content(
    category: str = Query(..., description="日志分类"),
    date: str = Query(..., description="日期 (YYYY-MM-DD)"),
    level: Optional[str] = Query(None, description="日志级别过滤"),
    search: Optional[str] = Query(None, description="搜索关键词"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    """获取日志内容"""
    try:
        cat = LogCategory(category)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"无效的日志分类: {category}")

    entries = _log_manager.read_log(cat, date, level=level, search=search, limit=limit, offset=offset)
    return {
        "category": category,
        "date": date,
        "total_returned": len(entries),
        "entries": [
            {
                "timestamp": e.timestamp,
                "level": e.level,
                "logger": e.logger,
                "message": e.message,
            }
            for e in entries
        ]
    }


@router.get("/logs/tail")
async def tail_log(
    category: str = Query(..., description="日志分类"),
    date: str = Query(..., description="日期 (YYYY-MM-DD)"),
    lines: int = Query(100, ge=1, le=500),
):
    """获取日志末尾 N 行"""
    try:
        cat = LogCategory(category)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"无效的日志分类: {category}")

    entries = _log_manager.tail_log(cat, date, lines)
    return {
        "category": category,
        "date": date,
        "lines": len(entries),
        "entries": [
            {
                "timestamp": e.timestamp,
                "level": e.level,
                "logger": e.logger,
                "message": e.message,
            }
            for e in entries
        ]
    }


@router.get("/logs/search")
async def search_logs(
    keyword: str = Query(..., description="搜索关键词"),
    category: Optional[str] = Query(None, description="日志分类过滤"),
    days: int = Query(7, ge=1, le=30),
    limit: int = Query(100, ge=1, le=500),
):
    """搜索日志"""
    cat = LogCategory(category) if category else None
    results = _log_manager.search_logs(keyword, cat, days, limit)
    return {"keyword": keyword, "total": len(results), "results": results}


@router.get("/logs/download/{category}/{date}")
async def download_log(category: str, date: str):
    """下载日志文件"""
    try:
        cat = LogCategory(category)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"无效的日志分类: {category}")

    content = _log_manager.download_log(cat, date)
    if content is None:
        raise HTTPException(status_code=404, detail="日志文件不存在")

    filename = f"{category}_{date}.log"
    return Response(
        content=content,
        media_type="text/plain",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@router.delete("/logs/cleanup")
async def cleanup_logs(keep_days: int = Query(30, ge=7, le=365)):
    """清理旧日志"""
    deleted = _log_manager.clear_old_logs(keep_days)
    return {"deleted_files": deleted, "keep_days": keep_days}
