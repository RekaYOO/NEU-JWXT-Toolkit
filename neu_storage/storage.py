"""
neu_storage/storage.py
======================
本地数据存储实现 - 当前目录存储

保存所有字段到 CSV
"""

import csv
import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Any

import sys
sys.path.insert(0, r"E:\code\NEUT")

from neu_academic.api import CourseScore, TermScores


@dataclass
class StorageConfig:
    """存储配置"""
    data_dir: str = ""           # 数据目录，空则使用当前目录下的 data/
    scores_filename: str = "scores.csv"
    config_filename: str = "config.json"
    
    def __post_init__(self):
        if not self.data_dir:
            # 使用当前工作目录下的 data/ 文件夹
            self.data_dir = os.path.join(os.getcwd(), "data")


class Storage:
    """本地数据存储管理器"""
    
    # CSV 所有字段定义（用于表头）- 注意：顺序很重要
    CSV_FIELDS = [
        "saved_at",              # 保存时间 - 放第一列避免BOM问题
        "course_code",           # KCH 课程号
        "course_name",           # KCM 课程名称
        "score",                 # XSZCJ 成绩（原始：数字或文字等级）
        "score_value",           # 计算值（等级课按绩点换算，匹配课用原成绩）
        "gpa",                   # JD 绩点
        "credit",                # XF 学分
        "term",                  # XNXQDM 学期代码
        "term_display",          # XNXQDM_DISPLAY 学期显示名
        "course_type",           # KCXZDM_DISPLAY 课程性质
        "course_category",       # KCLBDM_DISPLAY 课程类别
        "general_category",      # XGXKLBDM_DISPLAY 通识选修类别
        "exam_type",             # KSLXDM_DISPLAY 考核方式
        "exam_status",           # CXCKDM_DISPLAY 初修/重修
        "course_nature",         # KCXZDM 课程性质代码
        "is_passed",             # SFJG_DISPLAY 是否及格
        # 保留原始JSON数据
        "raw_data",              # 完整原始数据JSON
    ]
    
    def __init__(self, config: Optional[StorageConfig] = None):
        self.config = config or StorageConfig()
        self._ensure_dir()
    
    def _ensure_dir(self) -> None:
        """确保数据目录存在"""
        Path(self.config.data_dir).mkdir(parents=True, exist_ok=True)
    
    def _get_path(self, filename: str) -> str:
        """获取完整文件路径"""
        return os.path.join(self.config.data_dir, filename)
    
    def save_scores(self, scores: List[CourseScore], filename: Optional[str] = None,
                    metadata: Optional[Dict] = None) -> str:
        """
        保存成绩到 CSV - 包含所有字段
        
        Args:
            scores: 成绩列表
            filename: 自定义文件名
            metadata: 元数据（保存时间、来源等）
            
        Returns:
            保存的文件路径
        """
        filepath = self._get_path(filename or self.config.scores_filename)
        
        with open(filepath, 'w', newline='', encoding='utf-8-sig') as f:  # utf-8-sig 支持Excel
            writer = csv.DictWriter(f, fieldnames=self.CSV_FIELDS)
            writer.writeheader()
            
            for s in scores:
                row = {
                    "saved_at": datetime.now().isoformat(),
                    "course_code": s.code,
                    "course_name": s.name,
                    "score": s.score,
                    "score_value": s.get_score_value(),
                    "gpa": s.gpa,
                    "credit": s.credit,
                    "term": s.term,
                    "term_display": s.term_display,
                    "course_type": s.course_type,
                    "course_category": s.course_category,
                    "general_category": s.general_category,
                    "exam_type": s.exam_type,
                    "exam_status": s.exam_status,
                    "course_nature": s.course_nature,
                    "is_passed": "是" if s.is_passed else "否",
                    "raw_data": json.dumps(s.raw_data, ensure_ascii=False) if s.raw_data else "",
                }
                writer.writerow(row)
        
        # 同时保存元数据
        if metadata:
            meta_filepath = filepath.replace('.csv', '_meta.json')
            with open(meta_filepath, 'w', encoding='utf-8') as f:
                json.dump(metadata, f, ensure_ascii=False, indent=2)
        
        return filepath
    
    def load_scores(self, filename: Optional[str] = None) -> List[CourseScore]:
        """从 CSV 加载成绩 - 包含所有字段"""
        filepath = self._get_path(filename or self.config.scores_filename)
        
        if not os.path.exists(filepath):
            return []
        
        scores = []
        with open(filepath, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    # 解析 raw_data JSON
                    raw_data = {}
                    if row.get("raw_data"):
                        try:
                            raw_data = json.loads(row["raw_data"])
                        except:
                            pass
                    
                    score = CourseScore(
                        name=row.get("course_name", ""),
                        code=row.get("course_code", ""),
                        score=row.get("score", ""),  # 字符串成绩（可能是数字或文字等级）
                        gpa=float(row.get("gpa", 0) or 0),
                        credit=float(row.get("credit", 0) or 0),
                        term=row.get("term", ""),
                        term_display=row.get("term_display", ""),
                        course_type=row.get("course_type", ""),
                        course_category=row.get("course_category", ""),
                        general_category=row.get("general_category", ""),
                        exam_type=row.get("exam_type", ""),
                        exam_status=row.get("exam_status", ""),
                        course_nature=row.get("course_nature", ""),
                        is_passed=row.get("is_passed") == "是",
                        raw_data=raw_data
                    )
                    scores.append(score)
                except (ValueError, KeyError) as e:
                    print(f"跳过无效行: {e}")
                    continue
        
        return scores
    
    def load_scores_with_meta(self, filename: Optional[str] = None) -> Dict:
        """加载成绩及元数据"""
        scores = self.load_scores(filename)
        
        # 尝试加载元数据
        meta = {}
        filepath = self._get_path(filename or self.config.scores_filename)
        meta_filepath = filepath.replace('.csv', '_meta.json')
        
        if os.path.exists(meta_filepath):
            try:
                with open(meta_filepath, 'r', encoding='utf-8') as f:
                    meta = json.load(f)
            except:
                pass
        
        return {
            "scores": scores,
            "meta": meta,
            "filepath": filepath
        }
    
    def export_scores_by_term(self, term_scores: List[TermScores], 
                              base_filename: str = "scores") -> List[str]:
        """按学期导出多个 CSV 文件"""
        files = []
        
        for ts in term_scores:
            safe_term = ts.term_code.replace("/", "_").replace("\\", "_")
            filename = f"{base_filename}_{safe_term}.csv"
            
            meta = {
                "term_code": ts.term_code,
                "term_name": ts.term_name,
                "course_count": len(ts.courses),
                "total_credits": ts.total_credits,
                "gpa": ts.gpa,
                "exported_at": datetime.now().isoformat()
            }
            
            filepath = self.save_scores(ts.courses, filename, metadata=meta)
            files.append(filepath)
        
        return files
    
    def save_config(self, config: Dict[str, Any], filename: Optional[str] = None) -> str:
        """保存配置到 JSON"""
        filepath = self._get_path(filename or self.config.config_filename)
        
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        
        return filepath
    
    def load_config(self, filename: Optional[str] = None) -> Dict[str, Any]:
        """从 JSON 加载配置"""
        filepath = self._get_path(filename or self.config.config_filename)
        
        if not os.path.exists(filepath):
            return {}
        
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    # ── 头像缓存 ──────────────────────────────────────────────────────────────
    
    AVATAR_FILENAME = "avatar.png"
    AVATAR_META_FILENAME = "avatar_meta.json"
    
    def get_avatar_path(self) -> str:
        """获取头像文件路径"""
        return self._get_path(self.AVATAR_FILENAME)
    
    def save_avatar(self, avatar_data: bytes, username: str, avatar_token: str) -> str:
        """
        保存头像到本地
        
        Args:
            avatar_data: 头像图片二进制数据
            username: 用户名
            avatar_token: 头像 token（用于判断是否需要更新）
            
        Returns:
            保存的文件路径
        """
        filepath = self._get_path(self.AVATAR_FILENAME)
        
        # 保存图片
        with open(filepath, 'wb') as f:
            f.write(avatar_data)
        
        # 保存元数据
        meta = {
            "username": username,
            "avatar_token": avatar_token,
            "saved_at": datetime.now().isoformat(),
            "size_bytes": len(avatar_data)
        }
        self.save_config(meta, self.AVATAR_META_FILENAME)
        
        return filepath
    
    def load_avatar(self) -> Optional[bytes]:
        """
        从本地加载头像
        
        Returns:
            头像二进制数据，不存在返回 None
        """
        filepath = self._get_path(self.AVATAR_FILENAME)
        if not os.path.exists(filepath):
            return None
        
        with open(filepath, 'rb') as f:
            return f.read()
    
    def get_avatar_meta(self) -> Optional[Dict[str, Any]]:
        """获取头像元数据"""
        return self.load_config(self.AVATAR_META_FILENAME)
    
    def is_avatar_valid(self, current_username: str, current_avatar_token: str) -> bool:
        """
        检查本地头像是否有效
        
        Args:
            current_username: 当前用户名
            current_avatar_token: 当前头像 token
            
        Returns:
            头像是否有效（存在且未变更用户/token）
        """
        filepath = self._get_path(self.AVATAR_FILENAME)
        if not os.path.exists(filepath):
            return False
        
        meta = self.get_avatar_meta()
        if not meta:
            return False
        
        # 检查用户名和 token 是否匹配
        if meta.get("username") != current_username:
            return False
        if meta.get("avatar_token") != current_avatar_token:
            return False
        
        return True
    
    def clear_avatar(self) -> bool:
        """清除头像缓存"""
        try:
            avatar_path = self._get_path(self.AVATAR_FILENAME)
            meta_path = self._get_path(self.AVATAR_META_FILENAME)
            
            if os.path.exists(avatar_path):
                os.remove(avatar_path)
            if os.path.exists(meta_path):
                os.remove(meta_path)
            
            return True
        except Exception:
            return False
    
    def save_credentials(self, username: str, password: str) -> str:
        """保存登录凭证"""
        config = {
            "username": username,
            "password": password,
            "saved_at": datetime.now().isoformat()
        }
        return self.save_config(config, "credentials.json")
    
    def load_credentials(self) -> Optional[tuple]:
        """加载登录凭证"""
        config = self.load_config("credentials.json")
        
        if config.get("username") and config.get("password"):
            return (config["username"], config["password"])
        
        return None
    
    def clear_credentials(self) -> None:
        """清除登录凭证"""
        filepath = self._get_path("credentials.json")
        if os.path.exists(filepath):
            os.remove(filepath)
    
    def list_files(self) -> List[str]:
        """列出数据目录中的所有文件"""
        if not os.path.exists(self.config.data_dir):
            return []
        return os.listdir(self.config.data_dir)
    
    def get_storage_info(self) -> Dict[str, Any]:
        """获取存储信息"""
        files = self.list_files()
        total_size = 0
        csv_files = [f for f in files if f.endswith('.csv')]
        
        for f in files:
            filepath = os.path.join(self.config.data_dir, f)
            if os.path.isfile(filepath):
                total_size += os.path.getsize(filepath)
        
        return {
            "data_dir": self.config.data_dir,
            "file_count": len(files),
            "csv_count": len(csv_files),
            "total_size_bytes": total_size,
            "files": files
        }
    
    def get_last_update_time(self, filename: Optional[str] = None) -> Optional[datetime]:
        """获取文件最后更新时间"""
        filepath = self._get_path(filename or self.config.scores_filename)
        
        if not os.path.exists(filepath):
            return None
        
        mtime = os.path.getmtime(filepath)
        return datetime.fromtimestamp(mtime)
    
    def clear_all_data(self, preserve_config: bool = True) -> Dict[str, Any]:
        """
        清理所有数据文件
        
        Args:
            preserve_config: 是否保留配置文件（credentials.json, config.json）
            
        Returns:
            清理结果统计
        """
        if not os.path.exists(self.config.data_dir):
            return {"deleted": [], "preserved": [], "errors": []}
        
        # 定义需要保留的配置文件
        config_files = {"config.json", "credentials.json"} if preserve_config else set()
        
        deleted = []
        preserved = []
        errors = []
        
        for item in os.listdir(self.config.data_dir):
            item_path = os.path.join(self.config.data_dir, item)
            
            # 跳过目录（如 logs 子目录由日志系统管理）
            if os.path.isdir(item_path):
                continue
            
            if item in config_files:
                preserved.append(item)
                continue
            
            try:
                os.remove(item_path)
                deleted.append(item)
            except Exception as e:
                errors.append({"file": item, "error": str(e)})
        
        return {
            "deleted": deleted,
            "preserved": preserved,
            "errors": errors,
            "deleted_count": len(deleted),
            "preserved_count": len(preserved)
        }
    
    def save_json(self, data: Dict[str, Any], filename: str) -> str:
        """
        保存数据为 JSON
        
        Args:
            data: 要保存的数据
            filename: 文件名
            
        Returns:
            保存的文件路径
        """
        filepath = self._get_path(filename)
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return filepath
    
    def load_json(self, filename: str) -> Optional[Dict[str, Any]]:
        """
        从 JSON 加载数据
        
        Args:
            filename: 文件名
            
        Returns:
            数据字典，文件不存在返回 None
        """
        filepath = self._get_path(filename)
        if not os.path.exists(filepath):
            return None
        
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
