"""
neu_log/manager.py
==================
日志管理器

提供日志查看、搜索、下载功能
"""

import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Any, Optional, Iterator
from dataclasses import dataclass, field
from collections import deque

from .logger import LogCategory, LogConfig
from .presentation import present_log_message


@dataclass
class LogEntry:
    """日志条目"""
    timestamp: str
    level: str
    logger: str
    message: str
    raw_line: str
    event_type: str = "generic_system"
    event_title: str = "系统记录"
    summary: str = ""
    details: Dict[str, Any] = field(default_factory=dict)
    structured: bool = False


@dataclass
class LogFileInfo:
    """日志文件信息"""
    category: str
    date: str
    filename: str
    path: str
    size_bytes: int
    modified_time: datetime
    line_count: Optional[int] = None


class LogManager:
    """日志管理器"""
    
    def __init__(self, config: Optional[LogConfig] = None):
        self.config = config or LogConfig()

    def _segments(self, category: LogCategory, date: str) -> List[Path]:
        base = Path(self.config.get_log_path(category, date))
        rotated = []
        for path in base.parent.glob(f"{base.name}.*"):
            suffix = path.name[len(base.name) + 1:]
            if suffix.isdigit():
                rotated.append((int(suffix), path))
        return [path for _, path in sorted(rotated, reverse=True)] + (
            [base] if base.exists() else []
        )
    
    def get_log_files(
        self,
        category: Optional[LogCategory] = None,
        date: Optional[str] = None,
        days: int = 7
    ) -> List[LogFileInfo]:
        """
        获取日志文件列表
        
        Args:
            category: 日志分类过滤
            date: 指定日期 (YYYY-MM-DD)
            days: 最近几天
            
        Returns:
            LogFileInfo 列表
        """
        files = []
        log_dir = Path(self.config.log_dir)
        
        if not log_dir.exists():
            return files
        
        # 计算日期范围
        if date:
            target_dates = [date]
        else:
            target_dates = [
                (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
                for i in range(days)
            ]
        
        # 扫描日志目录
        for log_file in log_dir.glob("*.log"):
            # 解析文件名: category_YYYY-MM-DD.log
            match = re.match(r"(\w+)_(\d{4}-\d{2}-\d{2})\.log", log_file.name)
            if not match:
                continue
            
            cat, file_date = match.groups()
            
            # 分类过滤
            if category and cat != category.value:
                continue
            
            # 日期过滤
            if file_date not in target_dates:
                continue
            
            segments = self._segments(LogCategory(cat), file_date)
            stat = log_file.stat()
            files.append(LogFileInfo(
                category=cat,
                date=file_date,
                filename=log_file.name,
                path=str(log_file),
                size_bytes=sum(path.stat().st_size for path in segments),
                modified_time=datetime.fromtimestamp(stat.st_mtime),
            ))
        
        # 按日期排序（最新的在前）
        files.sort(key=lambda x: x.date, reverse=True)
        return files
    
    def read_log(
        self,
        category: LogCategory,
        date: str,
        level: Optional[str] = None,
        search: Optional[str] = None,
        limit: int = 1000,
        offset: int = 0
    ) -> List[LogEntry]:
        """
        读取日志内容
        
        Args:
            category: 日志分类
            date: 日期 (YYYY-MM-DD)
            level: 日志级别过滤 (DEBUG/INFO/WARNING/ERROR/CRITICAL)
            search: 搜索关键词
            limit: 最大返回条数
            offset: 跳过前 N 条
            
        Returns:
            LogEntry 列表
        """
        segments = self._segments(category, date)
        if not segments:
            return []
        
        entries = []
        
        for segment in segments:
            with segment.open('r', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue

                    entry = self._parse_log_line(line)
                    if not entry:
                        continue

                    if level and entry.level.upper() != level.upper():
                        continue

                    if search and search.lower() not in line.lower():
                        continue

                    entries.append(entry)

        # 日志查看器以最新记录为主：先合并轮转分片，再倒序应用分页。
        entries.reverse()
        return entries[offset:offset + limit]
    
    def tail_log(
        self,
        category: LogCategory,
        date: str,
        lines: int = 100
    ) -> List[LogEntry]:
        """
        读取日志末尾 N 行
        
        Args:
            category: 日志分类
            date: 日期
            lines: 行数
            
        Returns:
            LogEntry 列表
        """
        segments = self._segments(category, date)
        if not segments:
            return []
        last_lines = deque(maxlen=lines)
        for segment in segments:
            with segment.open('r', encoding='utf-8', errors='ignore') as handle:
                for line in handle:
                    if line.strip():
                        last_lines.append(line.strip())
        return list(reversed([self._parse_log_line(line) for line in last_lines]))
    
    def get_log_summary(self, days: int = 7) -> Dict[str, Any]:
        """
        获取日志统计摘要
        
        Args:
            days: 统计最近几天
            
        Returns:
            统计信息
        """
        summary = {
            "period_days": days,
            "categories": {},
            "total_size_bytes": 0,
            "total_files": 0,
        }
        
        for category in LogCategory:
            files = self.get_log_files(category=category, days=days)
            total_size = sum(f.size_bytes for f in files)
            
            summary["categories"][category.value] = {
                "file_count": len(files),
                "total_size_bytes": total_size,
                "files": [
                    {
                        "date": f.date,
                        "filename": f.filename,
                        "size_mb": round(f.size_bytes / 1024 / 1024, 2),
                    }
                    for f in files[:5]  # 只显示最近5个文件
                ]
            }
            
            summary["total_size_bytes"] += total_size
            summary["total_files"] += len(files)
        
        summary["total_size_mb"] = round(summary["total_size_bytes"] / 1024 / 1024, 2)
        
        return summary
    
    def search_logs(
        self,
        keyword: str,
        category: Optional[LogCategory] = None,
        days: int = 7,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        搜索日志
        
        Args:
            keyword: 搜索关键词
            category: 分类过滤
            days: 搜索最近几天
            limit: 最大结果数
            
        Returns:
            搜索结果列表
        """
        results = []
        files = self.get_log_files(category=category, days=days)
        
        for file_info in files:
            if len(results) >= limit:
                break
            
            entries = self.read_log(
                LogCategory(file_info.category),
                file_info.date,
                search=keyword,
                limit=limit - len(results)
            )
            
            for entry in entries:
                results.append({
                    "date": file_info.date,
                    "category": file_info.category,
                    "timestamp": entry.timestamp,
                    "level": entry.level,
                    "logger": entry.logger,
                    "message": entry.message,
                    "event_type": entry.event_type,
                    "event_title": entry.event_title,
                    "summary": entry.summary,
                    "details": entry.details or {},
                    "structured": entry.structured,
                })
        
        return results
    
    def clear_old_logs(self, keep_days: int = 30) -> int:
        """
        清理旧日志
        
        Args:
            keep_days: 保留最近几天的日志
            
        Returns:
            删除的文件数量
        """
        cutoff_date = datetime.now() - timedelta(days=keep_days)
        deleted_count = 0
        
        log_dir = Path(self.config.log_dir)
        if not log_dir.exists():
            return 0
        
        for log_file in log_dir.glob("*.log*"):
            match = re.match(r"\w+_(\d{4}-\d{2}-\d{2})\.log(?:\.\d+)?$", log_file.name)
            if not match:
                continue
            
            file_date = datetime.strptime(match.group(1), "%Y-%m-%d")
            if file_date < cutoff_date:
                log_file.unlink()
                deleted_count += 1
        
        return deleted_count
    
    def _parse_log_line(self, line: str) -> Optional[LogEntry]:
        """解析日志行"""
        # 标准格式: 2024-03-17 21:30:00 [INFO] logger.name: message
        pattern = r'^(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\s+\[(\w+)\]\s+(\S+):\s+(.*)$'
        match = re.match(pattern, line)
        
        if match:
            presentation = present_log_message(match.group(4), match.group(3))
            return LogEntry(
                timestamp=match.group(1),
                level=match.group(2),
                logger=match.group(3),
                message=match.group(4),
                raw_line=line,
                event_type=presentation.event_type,
                event_title=presentation.title,
                summary=presentation.summary,
                details=presentation.details,
                structured=presentation.structured,
            )
        
        # 无法解析，返回原始行
        presentation = present_log_message(line)
        return LogEntry(
            timestamp="",
            level="UNKNOWN",
            logger="",
            message=line,
            raw_line=line,
            event_type=presentation.event_type,
            event_title=presentation.title,
            summary=presentation.summary,
            details=presentation.details,
            structured=presentation.structured,
        )
    
    def download_log(self, category: LogCategory, date: str) -> Optional[bytes]:
        """
        下载日志文件
        
        Returns:
            文件内容 bytes，文件不存在返回 None
        """
        segments = self._segments(category, date)
        if not segments:
            return None
        return b"\n".join(path.read_bytes().rstrip(b"\n") for path in segments) + b"\n"
