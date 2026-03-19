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
