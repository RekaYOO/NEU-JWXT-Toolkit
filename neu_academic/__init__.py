"""
neu_academic - 东北大学教务系统成绩获取与计算包

使用示例：
    >>> from neu_auth import NEUAuthClient
    >>> 
    >>> auth = NEUAuthClient("学号", "密码")
    >>> auth.login()
    >>> 
    >>> # 获取成绩
    >>> scores = auth.academic.get_scores()
    >>> gpa = auth.academic.get_overall_gpa()
    >>> 
    >>> # 使用系统绩点计算
    >>> calculated_gpa = auth.academic.calculate_gpa(scores)
"""

from .api import AcademicAPI, CourseScore, TermScores
from .report import AcademicReportAPI, AcademicReport, CategoryInfo, CourseInfo

__version__ = "1.0.0"
__all__ = [
    "AcademicAPI", "CourseScore", "TermScores",
    "AcademicReportAPI", "AcademicReport", "CategoryInfo", "CourseInfo"
]
