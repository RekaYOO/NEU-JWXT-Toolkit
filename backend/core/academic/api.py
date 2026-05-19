"""
neu_academic/api.py
===================
成绩API - 挂载到 NEUAuthClient

使用系统返回的单科绩点进行计算
"""

import time
from typing import List, Dict, Optional, Any
from dataclasses import dataclass, field


@dataclass
class CourseScore:
    """课程成绩 - 包含所有原始字段"""
    # 基础字段（始终存在）
    name: str                    # KCM 课程名称
    code: str                    # KCH 课程号
    score: str                   # XSZCJ 成绩（原始字符串：数字或"优良中合格"）
    gpa: float                   # JD 绩点
    credit: float                # XF 学分
    term: str                    # XNXQDM 学期代码
    term_display: str            # XNXQDM_DISPLAY 学期显示名 "2024-2025学年春季学期"
    course_type: str             # KCXZDM_DISPLAY 课程性质 "必修/选修"
    course_category: str         # KCLBDM_DISPLAY 课程类别 "人文社会科学类"
    exam_type: str               # KSLXDM_DISPLAY 考核方式 "考试/考查"
    is_passed: bool              # SFJG_DISPLAY 是否及格
    
    # 扩展字段（可能有）
    exam_status: str = ""        # CXCKDM_DISPLAY 初修/重修
    general_category: str = ""   # XGXKLBDM_DISPLAY 通识选修类别 "科学素养类"
    course_nature: str = ""      # KCXZDM 课程性质代码
    
    # 原始数据（保留完整字段）
    raw_data: Dict[str, Any] = field(default_factory=dict)
    
    def _is_matching_score_gpa(self) -> bool:
        """检查成绩和绩点是否匹配（百分制换算关系）
        
        百分制绩点计算公式：绩点 = (分数 - 50) / 10
        允许 ±0.3 的误差（系统可能有四舍五入）
        """
        try:
            score_num = float(self.score)
            expected_gpa = (score_num - 50) / 10
            return abs(expected_gpa - self.gpa) < 0.3
        except (ValueError, TypeError):
            return False
    
    def get_score_value(self) -> float:
        """获取用于排序/计算的数值
        
        规则：
        - 成绩和绩点匹配（百分制）：直接返回数字成绩
        - 不匹配（等级课）：按绩点换算 (绩点+5)*10
        - 文字成绩：按绩点换算
        """
        # 尝试转为数字
        try:
            score_num = float(self.score)
            # 检查是否匹配百分制换算
            if self._is_matching_score_gpa():
                return score_num
            else:
                # 等级课：成绩和绩点不匹配，用绩点换算
                return (self.gpa + 5) * 10 if self.gpa else score_num
        except (ValueError, TypeError):
            # 文字成绩（如"优"、"合格"），按绩点换算
            return (self.gpa + 5) * 10 if self.gpa else 0


@dataclass  
class TermScores:
    """学期成绩"""
    term_code: str
    term_name: str
    courses: List[CourseScore]
    
    @property
    def total_credits(self) -> float:
        return sum(c.credit for c in self.courses)
    
    @property
    def gpa(self) -> float:
        """学期GPA = Σ(绩点×学分)/Σ学分"""
        total_credits = sum(c.credit for c in self.courses)
        if total_credits == 0:
            return 0.0
        total = sum(c.gpa * c.credit for c in self.courses)
        return total / total_credits


class AcademicAPI:
    """
    成绩API
    
    通过 NEUAuthClient.academic 访问
    """
    
    # API端点
    TERMS_URL = "https://jwxt.neu.edu.cn/jwapp/sys/cjzhcxapp/modules/wdcj/cxwdcjxnxq.do"
    SCORES_URL = "https://jwxt.neu.edu.cn/jwapp/sys/cjzhcxapp/modules/wdcj/cxwdcj.do"
    GPA_URL = "https://jwxt.neu.edu.cn/jwapp/sys/cjzhcxapp/api/wdcj/queryPjxfjd.do"
    
    # 请求头
    HEADERS = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Referer": "https://jwxt.neu.edu.cn/jwapp/sys/cjzhcxapp/*default/index.do",
        "X-Requested-With": "XMLHttpRequest",
    }
    
    def __init__(self, auth_client):
        """
        初始化
        
        Args:
            auth_client: NEUAuthClient 实例
        """
        self._client = auth_client
    
    def get_terms(self) -> List[Dict[str, str]]:
        """获取有成绩的学期列表"""
        try:
            resp = self._client.get(self.TERMS_URL, headers=self.HEADERS)
            data = resp.json()
            
            if data.get("code") == "0":
                rows = data["datas"]["cxwdcjxnxq"]["rows"]
                return [{"code": r["XNXQDM"], "name": r["XNXQMC"]} for r in rows]
        except Exception as e:
            logger.warning(f"获取学期列表失败: {e}")
        
        return []
    
    def get_scores(self, term: str = "") -> List[CourseScore]:
        """
        获取成绩列表 - 包含所有字段
        
        Args:
            term: 学期代码，空字符串表示全部
            
        Returns:
            CourseScore 列表
        """
        # 如果term为空，获取所有学期的成绩
        if not term:
            all_scores = []
            for t in self.get_terms():
                scores = self.get_scores(t["code"])
                all_scores.extend(scores)
                time.sleep(0.05)
            return all_scores
        
        # 按特定学期查询
        try:
            resp = self._client.post(
                self.SCORES_URL,
                data={"XNXQDM": term, "pageSize": "500", "pageNumber": "1"},
                headers=self.HEADERS
            )
            data = resp.json()
            
            if data.get("code") != "0":
                return []
            
            rows = data["datas"]["cxwdcj"]["rows"]
        except Exception as e:
            logger.warning(f"获取成绩失败: {e}")
            return []
        
        scores = []
        for row in rows:
            # 解析成绩 - 保留原始字符串（可能是数字或"优良中合格"）
            score_raw = str(row.get("XSZCJ", "")).strip()
            
            # 解析绩点
            try:
                gpa = float(row.get("JD", 0))
            except (ValueError, TypeError):
                gpa = 0.0
            
            # 解析学分
            try:
                credit = float(row.get("XF", 0))
            except (ValueError, TypeError):
                credit = 0.0
            
            # 构建 CourseScore，包含所有字段
            scores.append(CourseScore(
                name=row.get("KCM", ""),
                code=row.get("KCH", ""),
                score=score_raw,  # 保留原始成绩字符串
                gpa=gpa,
                credit=credit,
                term=row.get("XNXQDM", ""),
                term_display=row.get("XNXQDM_DISPLAY", row.get("XNXQDM", "")),
                course_type=row.get("KCXZDM_DISPLAY", ""),
                course_category=row.get("KCLBDM_DISPLAY", ""),
                exam_type=row.get("KSLXDM_DISPLAY", ""),
                is_passed=row.get("SFJG_DISPLAY") == "是",
                exam_status=row.get("CXCKDM_DISPLAY", ""),
                general_category=row.get("XGXKLBDM_DISPLAY", ""),
                course_nature=row.get("KCXZDM", ""),
                raw_data=dict(row)  # 保存完整原始数据
            ))
        
        return scores
    
    def get_overall_gpa(self) -> Optional[float]:
        """获取总绩点（系统计算）"""
        try:
            resp = self._client.get(self.GPA_URL, headers=self.HEADERS)
            data = resp.json()
            
            if data.get("code") == "0":
                return float(data["datas"]["queryPjxfjd"]["ZPJXFJD"])
        except Exception as e:
            logger.warning(f"获取总绩点失败: {e}")
        
        return None
    
    def calculate_gpa(self, scores: List[CourseScore]) -> float:
        """计算GPA"""
        if not scores:
            return 0.0
        
        total_credits = sum(c.credit for c in scores)
        if total_credits == 0:
            return 0.0
        
        total = sum(c.gpa * c.credit for c in scores)
        return total / total_credits
    
    def get_scores_by_term(self) -> List[TermScores]:
        """按学期分组获取成绩"""
        terms = self.get_terms()
        result = []
        
        for term in terms:
            scores = self.get_scores(term["code"])
            if scores:
                result.append(TermScores(
                    term_code=term["code"],
                    term_name=term["name"],
                    courses=scores
                ))
        
        return result


import logging
logger = logging.getLogger(__name__)
